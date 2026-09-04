from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.repositories import ApiIdempotencyRepository


API_IDEMPOTENCY_RUNNING = "running"
API_IDEMPOTENCY_SUCCEEDED = "succeeded"
API_IDEMPOTENCY_FAILED = "failed"


@dataclass(frozen=True)
class ApiIdempotencyClaim:
    request_scope: str
    idempotency_key: str
    request_fingerprint: str
    status: str
    owner: bool
    response_status_code: Optional[int]
    response_body: Optional[str]
    response_headers_json: Optional[str]


class ApiIdempotencyConflictError(RuntimeError):
    pass


class ApiIdempotencyStoreError(RuntimeError):
    pass


class ApiIdempotencyService:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def claim(
        self,
        *,
        request_scope: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> ApiIdempotencyClaim:
        normalized_scope = request_scope.strip()
        normalized_key = idempotency_key.strip()
        if not normalized_scope or not normalized_key:
            raise ApiIdempotencyStoreError("Idempotency scope and key are required")

        session = self._session_factory()
        repository = ApiIdempotencyRepository(session)
        try:
            existing = repository.get_by_scope_and_key(
                request_scope=normalized_scope,
                idempotency_key=normalized_key,
            )
            if existing is not None:
                return self._existing_claim(
                    existing,
                    request_fingerprint=request_fingerprint,
                )

            repository.create_running(
                request_scope=normalized_scope,
                idempotency_key=normalized_key,
                request_fingerprint=request_fingerprint,
            )
            session.commit()
            return ApiIdempotencyClaim(
                request_scope=normalized_scope,
                idempotency_key=normalized_key,
                request_fingerprint=request_fingerprint,
                status=API_IDEMPOTENCY_RUNNING,
                owner=True,
                response_status_code=None,
                response_body=None,
                response_headers_json=None,
            )
        except IntegrityError:
            session.rollback()
            existing = repository.get_by_scope_and_key(
                request_scope=normalized_scope,
                idempotency_key=normalized_key,
            )
            if existing is None:
                raise ApiIdempotencyStoreError(
                    "Idempotency claim conflicted but no record was found"
                )
            return self._existing_claim(
                existing,
                request_fingerprint=request_fingerprint,
            )
        except ApiIdempotencyConflictError:
            session.rollback()
            raise
        except Exception as exc:
            session.rollback()
            raise ApiIdempotencyStoreError("Idempotency claim failed") from exc
        finally:
            session.close()

    def complete(
        self,
        claim: ApiIdempotencyClaim,
        *,
        response_status_code: int,
        response_body: str,
        response_headers: dict[str, str],
    ) -> None:
        session = self._session_factory()
        repository = ApiIdempotencyRepository(session)
        try:
            record = repository.get_by_scope_and_key(
                request_scope=claim.request_scope,
                idempotency_key=claim.idempotency_key,
            )
            if record is None or record.request_fingerprint != claim.request_fingerprint:
                raise ApiIdempotencyStoreError("Idempotency record is missing or changed")
            status = (
                API_IDEMPOTENCY_SUCCEEDED
                if 200 <= response_status_code < 400
                else API_IDEMPOTENCY_FAILED
            )
            repository.complete(
                record,
                status=status,
                response_status_code=response_status_code,
                response_body=response_body,
                response_headers_json=json.dumps(response_headers, sort_keys=True),
            )
            session.commit()
        except Exception as exc:
            session.rollback()
            if isinstance(exc, ApiIdempotencyStoreError):
                raise
            raise ApiIdempotencyStoreError("Idempotency completion failed") from exc
        finally:
            session.close()

    @staticmethod
    def _existing_claim(
        record,
        *,
        request_fingerprint: str,
    ) -> ApiIdempotencyClaim:
        if record.request_fingerprint != request_fingerprint:
            raise ApiIdempotencyConflictError(
                "Idempotency-Key was reused with a different request payload"
            )
        return ApiIdempotencyClaim(
            request_scope=record.request_scope,
            idempotency_key=record.idempotency_key,
            request_fingerprint=record.request_fingerprint,
            status=record.status,
            owner=False,
            response_status_code=record.response_status_code,
            response_body=record.response_body,
            response_headers_json=record.response_headers_json,
        )
