#!/usr/bin/env python3
"""Dry-run or explicitly recover one committed Telegram proposal outcome."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
import sys
from typing import Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.app.dependencies import get_tool_registry
from src.db.session import get_db_session_factory
from src.services import TelegramRecoveryError, TelegramRecoveryService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or recover one Telegram callback business outcome safely."
    )
    parser.add_argument("--update-id", type=int, required=True)
    parser.add_argument("--workflow-id", type=int, required=True)
    parser.add_argument("--source-document-id", type=int, required=True)
    parser.add_argument("--change-request-id", type=int, required=True)
    parser.add_argument(
        "--action",
        choices=("resend-preview", "reconcile-success"),
        default="resend-preview",
    )
    parser.add_argument(
        "--chat-id",
        default=None,
        help="override the redacted workflow chat identity for explicit preview delivery",
    )
    parser.add_argument(
        "--delivery-confirmed",
        action="store_true",
        help="required for reconcile-success because no Telegram message is sent",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="commit the recovery; without this flag the command is dry-run",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if min(
        args.update_id,
        args.workflow_id,
        args.source_document_id,
        args.change_request_id,
    ) <= 0:
        return _error(args, "INVALID_ARGUMENT", "all ids must be positive")
    if args.action == "reconcile-success" and not args.delivery_confirmed:
        return _error(
            args,
            "INVALID_ARGUMENT",
            "--delivery-confirmed is required for reconcile-success",
        )

    service = TelegramRecoveryService(
        get_db_session_factory(),
        tool_registry=get_tool_registry(),
    )
    try:
        inspection = service.inspect(
            update_id=args.update_id,
            workflow_run_id=args.workflow_id,
            source_document_id=args.source_document_id,
            change_request_id=args.change_request_id,
        )
        if not inspection.eligible:
            return _emit(
                args,
                {
                    "status": "blocked",
                    "inspection": asdict(inspection),
                },
                exit_code=1,
            )
        if not args.apply:
            return _emit(
                args,
                {
                    "status": "dry_run",
                    "action": args.action,
                    "inspection": asdict(inspection),
                    "business_work_rerun": False,
                    "ocr_rerun": False,
                    "llm_rerun": False,
                    "source_document_recreated": False,
                    "change_request_recreated": False,
                },
            )

        delivery_status = "succeeded"
        recovery_action = "operator_confirmed_delivery"
        telegram_message_id = None
        if args.action == "resend-preview":
            chat_id = (args.chat_id or service.get_chat_id(workflow_run_id=args.workflow_id) or "").strip()
            if not chat_id:
                return _error(
                    args,
                    "CHAT_ID_REQUIRED",
                    "workflow metadata has no chat identity; pass --chat-id explicitly",
                )
            preview_text, _ = service.build_preview(
                change_request_id=args.change_request_id,
                source_document_id=args.source_document_id,
            )
            telegram_message_id = service.send_preview(
                chat_id=chat_id,
                workflow_run_id=args.workflow_id,
                preview_text=preview_text,
            )
            recovery_action = "preview_resent"
        reconciled = service.reconcile_success(
            update_id=args.update_id,
            workflow_run_id=args.workflow_id,
            source_document_id=args.source_document_id,
            change_request_id=args.change_request_id,
            preview_delivery_status=delivery_status,
            recovery_action=recovery_action,
            telegram_message_id=telegram_message_id,
        )
        return _emit(
            args,
            {
                "status": "reconciled",
                "action": args.action,
                "inspection": asdict(reconciled),
                "business_work_rerun": False,
                "ocr_rerun": False,
                "llm_rerun": False,
                "source_document_recreated": False,
                "change_request_recreated": False,
            },
        )
    except TelegramRecoveryError as exc:
        return _error(args, exc.error_code, exc.message)
    except Exception:
        return _error(
            args,
            "TELEGRAM_RECOVERY_STORAGE_UNAVAILABLE",
            "The recovery store is unavailable; no Telegram or business action was taken.",
        )


def _emit(args: argparse.Namespace, payload: dict, *, exit_code: int = 0) -> int:
    if args.json:
        print(json.dumps(payload, default=str, sort_keys=True))
    else:
        print(f"telegram outcome recovery {payload.get('status', 'unknown')}")
    return exit_code


def _error(args: argparse.Namespace, code: str, message: str) -> int:
    return _emit(
        args,
        {"status": "failed", "error_code": code, "message": message},
        exit_code=1,
    )


if __name__ == "__main__":
    raise SystemExit(main())
