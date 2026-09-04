from __future__ import annotations

import pytest
from pathlib import Path
import importlib.util
import subprocess
import sys

from src.app.config import get_settings
from src.app.dependencies import get_queue_client, get_readiness_service, get_tool_registry
from src.app.main import app
from src.services import ReadinessService
from src.tools import ToolNotFoundError


class _FakeReadinessProbe:
    def check_database(self) -> bool:
        return True

    def check_migration(self) -> bool:
        return True

    def check_vector_extension(self) -> bool:
        return True


class _UnavailableQueueClient:
    def is_available(self) -> bool:
        return False


def _load_preflight_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "preflight.py"
    spec = importlib.util.spec_from_file_location("foundation_preflight", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_active_app_excludes_legacy_routes() -> None:
    paths = {route.path for route in app.routes}

    assert "/api/qa" in paths
    assert "/api/ingest/source" in paths
    assert "/api/notion/index/page" in paths
    assert "/api/telegram/webhook" not in paths
    assert "/api/supplement/propose" not in paths
    assert "/api/supplement/accept" not in paths


def test_active_app_startup_does_not_import_legacy_modules() -> None:
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import src.app.main; "
                "legacy = sorted(name for name in sys.modules "
                "if name.startswith(('src.app.api.routes.telegram', "
                "'src.app.api.routes.supplement', 'src.orchestrators.telegram', "
                "'src.orchestrators.supplement', 'src.services.telegram', "
                "'src.tools.telegram_bot_tool', 'src.tools.notion_writer_tool', "
                "'src.queue', 'src.repositories.telegram_update_ledger'))); "
                "print('\\n'.join(legacy)); raise SystemExit(bool(legacy))"
            ),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert probe.returncode == 0, probe.stdout + probe.stderr


def test_default_tool_registry_excludes_legacy_write_and_telegram_tools(
    monkeypatch,
) -> None:
    get_settings.cache_clear()
    get_tool_registry.cache_clear()
    monkeypatch.delenv("NOTION_BACKEND", raising=False)
    monkeypatch.delenv("NOTION_TOKEN", raising=False)

    try:
        registry = get_tool_registry()

        assert set(registry.list_tool_names()) == {
            "image_ocr_parser",
            "notion_reader",
            "pdf_parser",
            "url_article_parser",
            "youtube_transcript_parser",
        }
        with pytest.raises(ToolNotFoundError):
            registry.get_tool("notion_writer")
        with pytest.raises(ToolNotFoundError):
            registry.get_tool("telegram_bot")
    finally:
        get_settings.cache_clear()
        get_tool_registry.cache_clear()


def test_core_readiness_does_not_require_redis_or_rq() -> None:
    service = ReadinessService(
        probe=_FakeReadinessProbe(),
        mode="local",
        openai_configured=True,
        queue_client=_UnavailableQueueClient(),
        queue_required=False,
    )

    report = service.check()

    assert report.is_ready is True
    assert "queue" not in report.checks


def test_readiness_factory_does_not_construct_legacy_queue(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "test")

    def fail_if_queue_is_constructed():
        raise AssertionError("core readiness must not construct a queue client")

    monkeypatch.setattr(
        "src.app.dependencies.get_queue_client",
        fail_if_queue_is_constructed,
    )

    try:
        readiness_service = get_readiness_service()
    finally:
        get_settings.cache_clear()

    assert isinstance(readiness_service, ReadinessService)


def test_api_preflight_does_not_require_rq() -> None:
    preflight = _load_preflight_module()

    assert ("rq", "rq") not in preflight.PROFILE_DEPENDENCIES["api"]
    assert ("rq", "rq") in preflight.PROFILE_DEPENDENCIES["legacy-worker"]
