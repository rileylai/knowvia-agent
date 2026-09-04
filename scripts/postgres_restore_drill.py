#!/usr/bin/env python3
"""Run an explicitly-confirmed PostgreSQL backup and restore drill.

The default mode only prints a safe plan. A live run creates two uniquely named
databases, applies the real Alembic migrations, dumps one database with
``pg_dump``, restores it with ``pg_restore``, verifies a sentinel and the
migration revision, and removes only those generated databases.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.app.config import get_settings  # noqa: E402

ADMIN_DATABASE_URL_ENV = "LEARNLOOP_RESTORE_DRILL_ADMIN_DATABASE_URL"
CONFIRMATION_TEXT = "DISPOSABLE"
SENTINEL_TABLE = "learnloop_restore_drill_sentinel"
SENTINEL_MARKER = "restore-drill-ok"


class RestoreDrillError(Exception):
    """A fixed, safe operator-facing restore drill failure."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


@dataclass(frozen=True)
class RestoreDrillPlan:
    source_database_name: str
    restored_database_name: str
    required_commands: List[str]
    destructive_scope: str


def build_restore_drill_plan(*, run_id: Optional[str] = None) -> RestoreDrillPlan:
    suffix = (run_id or uuid.uuid4().hex)[:12]
    return RestoreDrillPlan(
        source_database_name=f"learnloop_restore_src_{suffix}",
        restored_database_name=f"learnloop_restore_dst_{suffix}",
        required_commands=["pg_dump", "pg_restore"],
        destructive_scope="generated temporary databases only",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a disposable PostgreSQL backup/restore drill safely."
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="execute the live drill; without this flag the command is dry-run",
    )
    parser.add_argument(
        "--confirm-disposable-target",
        default=None,
        help=f"required live-run confirmation: {CONFIRMATION_TEXT}",
    )
    parser.add_argument(
        "--admin-database-url",
        default=None,
        help=(
            "explicit PostgreSQL admin URL; otherwise read "
            f"{ADMIN_DATABASE_URL_ENV}"
        ),
    )
    parser.add_argument("--json", action="store_true", help="emit redacted JSON")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    plan = build_restore_drill_plan()

    if not args.run:
        return _print_payload(
            args,
            {
                "status": "dry_run",
                "required_opt_in": [
                    "--run",
                    f"--confirm-disposable-target {CONFIRMATION_TEXT}",
                    f"--admin-database-url <value> or {ADMIN_DATABASE_URL_ENV}",
                ],
                "plan": asdict(plan),
            },
        )

    if args.confirm_disposable_target != CONFIRMATION_TEXT:
        return _print_error(args, "DISPOSABLE_TARGET_CONFIRMATION_REQUIRED")

    admin_database_url = args.admin_database_url or os.getenv(ADMIN_DATABASE_URL_ENV)
    if not admin_database_url:
        return _print_error(args, "ADMIN_DATABASE_URL_REQUIRED")

    missing_commands = [
        command_name
        for command_name in plan.required_commands
        if shutil.which(command_name) is None
    ]
    if missing_commands:
        return _print_error(args, "POSTGRES_CLIENT_COMMAND_MISSING")

    try:
        result = run_restore_drill(
            admin_database_url=admin_database_url,
            plan=plan,
        )
    except RestoreDrillError as exc:
        return _print_error(args, exc.error_code)

    return _print_payload(args, result)


def run_restore_drill(
    *,
    admin_database_url: str,
    plan: RestoreDrillPlan,
) -> Dict[str, Any]:
    """Execute the live drill against only generated database names."""

    admin_engine: Optional[Engine] = None
    created_database_names: List[str] = []
    primary_error: Optional[RestoreDrillError] = None
    cleanup_failed = False

    try:
        admin_engine = create_engine(admin_database_url, isolation_level="AUTOCOMMIT")
        _create_database(admin_engine, plan.source_database_name)
        created_database_names.append(plan.source_database_name)
        _create_database(admin_engine, plan.restored_database_name)
        created_database_names.append(plan.restored_database_name)

        source_database_url = _database_url_for_name(
            admin_database_url,
            plan.source_database_name,
        )
        restored_database_url = _database_url_for_name(
            admin_database_url,
            plan.restored_database_name,
        )
        _apply_migrations(source_database_url)
        _seed_sentinel(source_database_url)

        with tempfile.TemporaryDirectory(prefix="learnloop-restore-drill-") as temp_dir:
            archive_path = Path(temp_dir) / "learnloop.backup"
            _run_pg_command(
                "PG_DUMP_FAILED",
                _build_pg_dump_args(source_database_url, archive_path),
                database_url=source_database_url,
            )
            _run_pg_command(
                "PG_RESTORE_FAILED",
                _build_pg_restore_args(restored_database_url, archive_path),
                database_url=restored_database_url,
            )

        _apply_migrations(restored_database_url)
        _verify_restored_database(restored_database_url)
    except RestoreDrillError as exc:
        primary_error = exc
    except Exception as exc:
        _ = exc
        primary_error = RestoreDrillError("POSTGRES_RESTORE_DRILL_FAILED")
    finally:
        if admin_engine is not None:
            admin_engine.dispose()
        cleanup_failed = not _drop_generated_databases(
            admin_database_url,
            created_database_names,
        )

    if primary_error is not None:
        raise primary_error
    if cleanup_failed:
        raise RestoreDrillError("POSTGRES_RESTORE_DRILL_CLEANUP_FAILED")

    return {
        "status": "passed",
        "checks": [
            "real Alembic migrations applied to source database",
            "custom-format pg_dump completed",
            "pg_restore completed into a separate database",
            "migration revision and restore sentinel verified",
        ],
        "cleanup": "generated temporary databases removed",
    }


def _create_database(admin_engine: Engine, database_name: str) -> None:
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))


def _drop_generated_databases(
    admin_database_url: str,
    database_names: Sequence[str],
) -> bool:
    if not database_names:
        return True
    try:
        cleanup_engine = create_engine(admin_database_url, isolation_level="AUTOCOMMIT")
        try:
            for database_name in reversed(database_names):
                with cleanup_engine.connect() as connection:
                    connection.execute(
                        text(f'DROP DATABASE "{database_name}" WITH (FORCE)')
                    )
        finally:
            cleanup_engine.dispose()
    except Exception:
        return False
    return True


def _database_url_for_name(admin_database_url: str, database_name: str) -> str:
    return make_url(admin_database_url).set(database=database_name).render_as_string(
        hide_password=False
    )


def _apply_migrations(database_url: str) -> None:
    previous_database_url = os.getenv("DATABASE_URL")
    try:
        os.environ["DATABASE_URL"] = database_url
        get_settings.cache_clear()
        config = Config(str((_REPO_ROOT / "alembic.ini").resolve()))
        config.set_main_option(
            "script_location",
            str((_REPO_ROOT / "alembic").resolve()),
        )
        command.upgrade(config, "head")
    finally:
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        get_settings.cache_clear()


def _seed_sentinel(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE TABLE {SENTINEL_TABLE} ("
                    "id INTEGER PRIMARY KEY, marker TEXT NOT NULL)"
                )
            )
            connection.execute(
                text(
                    f"INSERT INTO {SENTINEL_TABLE} (id, marker) "
                    "VALUES (1, :marker)"
                ),
                {"marker": SENTINEL_MARKER},
            )
    finally:
        engine.dispose()


def _verify_restored_database(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            marker = connection.execute(
                text(f"SELECT marker FROM {SENTINEL_TABLE} WHERE id = 1")
            ).scalar_one_or_none()
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
        if marker != SENTINEL_MARKER or not revision:
            raise RestoreDrillError("RESTORE_VERIFICATION_FAILED")
    finally:
        engine.dispose()


def _build_pg_dump_args(database_url: str, archive_path: Path) -> List[str]:
    return [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--file",
        str(archive_path),
        *_libpq_args(database_url),
    ]


def _build_pg_restore_args(database_url: str, archive_path: Path) -> List[str]:
    return [
        "pg_restore",
        "--exit-on-error",
        "--no-owner",
        "--dbname",
        _database_name(database_url),
        *_libpq_args(database_url, include_database=False),
        str(archive_path),
    ]


def _libpq_args(database_url: str, *, include_database: bool = True) -> List[str]:
    url = make_url(database_url)
    args: List[str] = []
    if url.host:
        args.extend(["--host", url.host])
    if url.port:
        args.extend(["--port", str(url.port)])
    if url.username:
        args.extend(["--username", url.username])
    if include_database:
        args.extend(["--dbname", _database_name(database_url)])
    return args


def _database_name(database_url: str) -> str:
    database_name = make_url(database_url).database
    if not database_name:
        raise RestoreDrillError("DATABASE_NAME_REQUIRED")
    return database_name


def _run_pg_command(
    error_code: str,
    command_args: Sequence[str],
    *,
    database_url: str,
) -> None:
    environment = os.environ.copy()
    password = make_url(database_url).password
    if password:
        environment["PGPASSWORD"] = password
    try:
        completed = subprocess.run(
            list(command_args),
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
    except (OSError, ValueError) as exc:
        _ = exc
        raise RestoreDrillError(error_code) from None
    if completed.returncode != 0:
        raise RestoreDrillError(error_code)


def _print_payload(args: argparse.Namespace, payload: Dict[str, Any]) -> int:
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"postgres restore drill {payload['status']}")
    return 0


def _print_error(args: argparse.Namespace, error_code: str) -> int:
    payload = {"status": "failed", "error_code": error_code}
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(f"postgres restore drill failed: {error_code}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
