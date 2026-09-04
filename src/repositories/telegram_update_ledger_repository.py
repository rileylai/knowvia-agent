from __future__ import annotations

from typing import Optional

from src.db.models import TelegramUpdateLedger


class TelegramUpdateLedgerRepository:
    def __init__(self, session) -> None:
        self._session = session

    def get_by_update_id(self, update_id: int) -> Optional[TelegramUpdateLedger]:
        return self._session.get(TelegramUpdateLedger, update_id)

    def create_running(self, update_id: int) -> TelegramUpdateLedger:
        ledger = TelegramUpdateLedger(update_id=update_id, status="running")
        self._session.add(ledger)
        self._session.flush()
        return ledger

    def mark_succeeded(
        self,
        update_id: int,
        *,
        workflow_run_id: Optional[int],
        result_json: str,
    ) -> Optional[TelegramUpdateLedger]:
        ledger = self.get_by_update_id(update_id)
        if ledger is None:
            return None
        ledger.status = "succeeded"
        ledger.workflow_run_id = workflow_run_id
        ledger.result_json = result_json
        ledger.failure_json = None
        self._session.flush()
        return ledger

    def mark_failed(
        self,
        update_id: int,
        *,
        workflow_run_id: Optional[int],
        failure_json: str,
    ) -> Optional[TelegramUpdateLedger]:
        ledger = self.get_by_update_id(update_id)
        if ledger is None:
            return None
        ledger.status = "failed"
        ledger.workflow_run_id = workflow_run_id
        ledger.result_json = None
        ledger.failure_json = failure_json
        self._session.flush()
        return ledger
