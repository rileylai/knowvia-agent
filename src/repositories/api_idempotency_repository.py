from __future__ import annotations

from typing import Optional

from sqlalchemy import func

from src.db.models import ApiIdempotencyRecord


class ApiIdempotencyRepository:
    def __init__(self, session) -> None:
        self._session = session

    def get_by_scope_and_key(
        self,
        *,
        request_scope: str,
        idempotency_key: str,
    ) -> Optional[ApiIdempotencyRecord]:
        return (
            self._session.query(ApiIdempotencyRecord)
            .filter(
                ApiIdempotencyRecord.request_scope == request_scope,
                ApiIdempotencyRecord.idempotency_key == idempotency_key,
            )
            .one_or_none()
        )

    def _allocate_id_for_sqlite(self) -> int:
        max_id = self._session.query(func.max(ApiIdempotencyRecord.id)).scalar()
        return int(max_id or 0) + 1

    def create_running(
        self,
        *,
        request_scope: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> ApiIdempotencyRecord:
        record = ApiIdempotencyRecord(
            request_scope=request_scope,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            status="running",
        )
        if self._session.bind is not None and self._session.bind.dialect.name == "sqlite":
            record.id = self._allocate_id_for_sqlite()
        self._session.add(record)
        self._session.flush()
        return record

    def complete(
        self,
        record: ApiIdempotencyRecord,
        *,
        status: str,
        response_status_code: int,
        response_body: str,
        response_headers_json: str,
    ) -> ApiIdempotencyRecord:
        record.status = status
        record.response_status_code = response_status_code
        record.response_body = response_body
        record.response_headers_json = response_headers_json
        self._session.flush()
        return record
