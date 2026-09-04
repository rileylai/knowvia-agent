from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.repositories import TelegramUpdateLedgerRepository


TELEGRAM_UPDATE_RUNNING = "running"
TELEGRAM_UPDATE_SUCCEEDED = "succeeded"
TELEGRAM_UPDATE_FAILED = "failed"


@dataclass(frozen=True)
class TelegramUpdateClaim:
    update_id: int
    status: str
    owner: bool
    workflow_run_id: Optional[int]
    result_json: Optional[str]
    failure_json: Optional[str]


class TelegramUpdateIdempotencyError(RuntimeError):
    pass


class TelegramUpdateIdempotencyService:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def claim(self, update_id: Optional[int]) -> Optional[TelegramUpdateClaim]:
        if update_id is None:
            return None

        normalized_update_id = int(update_id)
        session = self._session_factory()
        repository = TelegramUpdateLedgerRepository(session)
        try:
            existing = repository.get_by_update_id(normalized_update_id)
            if existing is not None:
                return self._claim_from_row(existing, owner=False)

            repository.create_running(normalized_update_id)
            session.commit()
            return TelegramUpdateClaim(
                update_id=normalized_update_id,
                status=TELEGRAM_UPDATE_RUNNING,
                owner=True,
                workflow_run_id=None,
                result_json=None,
                failure_json=None,
            )
        except IntegrityError:
            session.rollback()
            existing = repository.get_by_update_id(normalized_update_id)
            if existing is None:
                raise TelegramUpdateIdempotencyError(
                    "Telegram update claim conflicted but no ledger row was found"
                )
            return self._claim_from_row(existing, owner=False)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def mark_succeeded(
        self,
        update_id: Optional[int],
        *,
        workflow_run_id: Optional[int],
        result: dict,
    ) -> None:
        if update_id is None:
            return
        self._update(
            update_id=int(update_id),
            operation=lambda repository: repository.mark_succeeded(
                int(update_id),
                workflow_run_id=workflow_run_id,
                result_json=json.dumps(result, sort_keys=True),
            ),
        )

    def mark_failed(
        self,
        update_id: Optional[int],
        *,
        workflow_run_id: Optional[int],
        failure: dict,
    ) -> None:
        if update_id is None:
            return
        self._update(
            update_id=int(update_id),
            operation=lambda repository: repository.mark_failed(
                int(update_id),
                workflow_run_id=workflow_run_id,
                failure_json=json.dumps(failure, sort_keys=True),
            ),
        )

    def _update(self, *, update_id: int, operation) -> None:
        session = self._session_factory()
        repository = TelegramUpdateLedgerRepository(session)
        try:
            ledger = operation(repository)
            if ledger is None:
                raise TelegramUpdateIdempotencyError(
                    f"Telegram update ledger row not found: update_id={update_id}"
                )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _claim_from_row(row, *, owner: bool) -> TelegramUpdateClaim:
        return TelegramUpdateClaim(
            update_id=int(row.update_id),
            status=row.status,
            owner=owner,
            workflow_run_id=row.workflow_run_id,
            result_json=row.result_json,
            failure_json=row.failure_json,
        )
