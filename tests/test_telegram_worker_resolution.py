from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import fakeredis
from rq import Queue, SimpleWorker
from rq.utils import import_attribute
from rq.worker import SpawnWorker, Worker

from src.orchestrators.telegram_gateway_orchestrator import TelegramGatewayResult
from src.queue import RQQueueClient, get_callable_import_path
import src.worker.telegram as telegram_worker
from src.worker.telegram import (
    TELEGRAM_FULL_INDEX_JOB_PATH,
    TELEGRAM_WEBHOOK_JOB_PATH,
    process_telegram_full_index_job,
    process_telegram_webhook_job,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKER_SCRIPT = REPO_ROOT / "scripts" / "run_worker.py"


def _worker_script_namespace() -> dict[str, object]:
    import runpy

    return runpy.run_path(str(WORKER_SCRIPT), run_name="worker_policy_test")


def _subprocess_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    return environment


def test_fresh_subprocess_imports_module_level_worker_callable() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from src.worker.telegram import "
                "TELEGRAM_WEBHOOK_JOB_PATH, process_telegram_webhook_job; "
                "assert callable(process_telegram_webhook_job); "
                "assert process_telegram_webhook_job.__module__ == "
                "'src.worker.telegram'; "
                "assert TELEGRAM_WEBHOOK_JOB_PATH == "
                "'src.worker.telegram.process_telegram_webhook_job'"
            ),
        ],
        cwd=REPO_ROOT,
        env=_subprocess_environment(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_non_repo_cwd_worker_bootstrap_resolves_job_function(tmp_path: Path) -> None:
    probe = textwrap.dedent(
        """
        import runpy
        import sys

        namespace = runpy.run_path(sys.argv[1], run_name="worker_import_probe")
        namespace["validate_telegram_job_import"]()
        assert namespace["ensure_repo_root_on_sys_path"]().__str__() in sys.path
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe, str(WORKER_SCRIPT)],
        cwd=tmp_path,
        env=_subprocess_environment(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_worker_startup_validates_import_before_redis_requirement(
    tmp_path: Path,
) -> None:
    environment = _subprocess_environment()
    environment.pop("REDIS_URL", None)
    result = subprocess.run(
        [sys.executable, str(WORKER_SCRIPT)],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "REDIS_URL is required" in result.stderr
    assert "Invalid attribute name" not in result.stderr


def test_darwin_uses_spawn_worker() -> None:
    select_worker_class = _worker_script_namespace()["select_worker_class"]

    assert select_worker_class(system_name="Darwin", requested="auto") is SpawnWorker
    assert select_worker_class(system_name="Darwin", requested="spawn") is SpawnWorker


def test_linux_uses_standard_worker() -> None:
    select_worker_class = _worker_script_namespace()["select_worker_class"]

    assert select_worker_class(system_name="Linux", requested="auto") is Worker
    assert select_worker_class(system_name="Linux", requested="worker") is Worker


def test_darwin_rejects_fork_worker_override() -> None:
    select_worker_class = _worker_script_namespace()["select_worker_class"]

    try:
        select_worker_class(system_name="Darwin", requested="worker")
    except ValueError as exc:
        assert "disabled on macOS" in str(exc)
    else:
        raise AssertionError("Darwin must not select the fork-based Worker")


def test_worker_entrypoint_enables_embedded_scheduler() -> None:
    namespace = _worker_script_namespace()

    class _Worker:
        def __init__(self) -> None:
            self.calls = []

        def work(self, **kwargs):
            self.calls.append(kwargs)
            return True

    worker = _Worker()
    assert namespace["run_worker"](worker=worker, burst=False) is True
    assert worker.calls == [{"burst": False, "with_scheduler": True}]


def test_rq_import_attribute_resolves_canonical_worker_path() -> None:
    resolved = import_attribute(TELEGRAM_WEBHOOK_JOB_PATH)

    assert resolved is process_telegram_webhook_job
    assert get_callable_import_path(process_telegram_webhook_job) == (
        TELEGRAM_WEBHOOK_JOB_PATH
    )
    assert import_attribute(TELEGRAM_FULL_INDEX_JOB_PATH) is process_telegram_full_index_job
    assert get_callable_import_path(process_telegram_full_index_job) == (
        TELEGRAM_FULL_INDEX_JOB_PATH
    )


def test_enqueue_path_matches_actual_worker_callable() -> None:
    connection = fakeredis.FakeRedis()
    enqueued = RQQueueClient(connection=connection).enqueue(
        queue_name="telegram",
        function=process_telegram_webhook_job,
        args=(None, "555", "/help", None, None, [], "worker-test"),
    )
    job = Queue(name="telegram", connection=connection).fetch_job(enqueued.job_id)

    assert job is not None
    assert job.func_name == TELEGRAM_WEBHOOK_JOB_PATH
    assert enqueued.function_name == TELEGRAM_WEBHOOK_JOB_PATH
    assert import_attribute(job.func_name) is process_telegram_webhook_job


def test_rq_job_resolves_and_enters_gateway_stub(monkeypatch) -> None:
    class StubSession:
        closed = False

        def close(self) -> None:
            self.closed = True

    class StubGateway:
        def __init__(self) -> None:
            self.received = None

        async def handle_claimed_webhook(self, **kwargs):
            self.received = kwargs
            return TelegramGatewayResult(
                workflow_run_id=None,
                status="running",
                handled=False,
                command="/help",
                reply_text=None,
                telegram_message_id=None,
                skipped_reason="STUB",
                source_document_id=None,
                change_request_id=None,
                source_type=None,
                target_notion_page_id=None,
                qa_workflow_run_id=None,
                insufficient_info=None,
                citations=[],
                review_workflow_run_id=None,
                review_action=None,
                change_request_status=None,
            )

    session = StubSession()
    gateway = StubGateway()
    monkeypatch.setattr(
        telegram_worker,
        "get_db_session_factory",
        lambda: (lambda: session),
    )
    monkeypatch.setattr(
        telegram_worker,
        "build_telegram_gateway_orchestrator",
        lambda **_: gateway,
    )

    connection = fakeredis.FakeRedis()
    enqueued = RQQueueClient(connection=connection).enqueue(
        queue_name="telegram",
        function=process_telegram_webhook_job,
        args=(8011, "555", "/help", None, None, [], "gateway-stub-test"),
    )
    SimpleWorker(
        [Queue(name="telegram", connection=connection)],
        connection=connection,
    ).work(burst=True)

    job = Queue(name="telegram", connection=connection).fetch_job(enqueued.job_id)
    assert job is not None
    assert job.is_finished
    assert job.return_value()["status"] == "running"
    assert gateway.received is not None
    assert gateway.received["update_id"] == 8011
    assert gateway.received["text"] == "/help"
    assert session.closed is True
