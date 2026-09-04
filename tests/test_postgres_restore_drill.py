from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys


def _load_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "postgres_restore_drill.py"
    spec = importlib.util.spec_from_file_location("learnloop_postgres_restore_drill", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_restore_drill_is_dry_run_by_default(capsys) -> None:
    module = _load_module()

    assert module.main(["--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dry_run"
    assert "--run" in payload["required_opt_in"]
    assert payload["plan"]["destructive_scope"] == "generated temporary databases only"


def test_restore_drill_requires_explicit_confirmation_and_admin_url(capsys) -> None:
    module = _load_module()

    assert module.main(["--run", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == (
        "DISPOSABLE_TARGET_CONFIRMATION_REQUIRED"
    )

    assert module.main(
        ["--run", "--confirm-disposable-target", "DISPOSABLE", "--json"]
    ) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == (
        "ADMIN_DATABASE_URL_REQUIRED"
    )


def test_restore_plan_uses_unique_generated_database_names() -> None:
    module = _load_module()

    plan = module.build_restore_drill_plan(run_id="abcdef123456")

    assert plan.source_database_name == "learnloop_restore_src_abcdef123456"
    assert plan.restored_database_name == "learnloop_restore_dst_abcdef123456"
    assert plan.source_database_name != plan.restored_database_name


def test_pg_commands_do_not_put_database_password_in_arguments() -> None:
    module = _load_module()
    database_url = "postgresql+psycopg://operator:secret-value@localhost:5432/restore"

    dump_args = module._build_pg_dump_args(
        database_url,
        Path("/tmp/learnloop-restore-drill.dump"),
    )
    restore_args = module._build_pg_restore_args(
        database_url,
        Path("/tmp/learnloop-restore-drill.dump"),
    )

    assert "secret-value" not in dump_args
    assert "secret-value" not in restore_args
