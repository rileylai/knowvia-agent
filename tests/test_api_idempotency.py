from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.app.api_idempotency import api_idempotency_middleware
from src.db.base import Base
from src.db.models import ApiIdempotencyRecord
from src.services import (
    API_IDEMPOTENCY_FAILED,
    API_IDEMPOTENCY_RUNNING,
    API_IDEMPOTENCY_SUCCEEDED,
    ApiIdempotencyConflictError,
    ApiIdempotencyService,
)


def _build_session_factory(database_url: str = "sqlite+pysqlite:///:memory:"):
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool if ":memory:" in database_url else None,
    )
    Base.metadata.create_all(engine, tables=[ApiIdempotencyRecord.__table__])
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def test_service_replays_completed_response_and_rejects_payload_conflict() -> None:
    service = ApiIdempotencyService(_build_session_factory())

    owner = service.claim(
        request_scope="POST:/api/ingest/source",
        idempotency_key="request-1",
        request_fingerprint="fingerprint-a",
    )
    assert owner.owner is True
    assert owner.status == API_IDEMPOTENCY_RUNNING

    running_duplicate = service.claim(
        request_scope="POST:/api/ingest/source",
        idempotency_key="request-1",
        request_fingerprint="fingerprint-a",
    )
    assert running_duplicate.owner is False
    assert running_duplicate.status == API_IDEMPOTENCY_RUNNING

    service.complete(
        owner,
        response_status_code=201,
        response_body='{"source_document_id": 7}',
        response_headers={"content-type": "application/json"},
    )
    replay = service.claim(
        request_scope="POST:/api/ingest/source",
        idempotency_key="request-1",
        request_fingerprint="fingerprint-a",
    )
    assert replay.owner is False
    assert replay.status == API_IDEMPOTENCY_SUCCEEDED
    assert replay.response_status_code == 201
    assert replay.response_body == '{"source_document_id": 7}'

    try:
        service.claim(
            request_scope="POST:/api/ingest/source",
            idempotency_key="request-1",
            request_fingerprint="fingerprint-b",
        )
    except ApiIdempotencyConflictError:
        pass
    else:
        raise AssertionError("different payload must conflict")


def test_concurrent_claims_have_one_owner(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'api-idempotency.db'}"
    session_factory = _build_session_factory(database_url)
    barrier = Barrier(2)

    def synchronized_factory():
        barrier.wait()
        return session_factory()

    service = ApiIdempotencyService(synchronized_factory)
    with ThreadPoolExecutor(max_workers=2) as executor:
        claims = list(
            executor.map(
                lambda _: service.claim(
                    request_scope="POST:/api/supplement/propose",
                    idempotency_key="request-2",
                    request_fingerprint="fingerprint-a",
                ),
                range(2),
            )
        )

    assert sum(claim.owner for claim in claims) == 1
    assert {claim.status for claim in claims} == {API_IDEMPOTENCY_RUNNING}
    session = session_factory()
    try:
        assert session.query(func.count(ApiIdempotencyRecord.id)).scalar() == 1
    finally:
        session.close()


def _build_test_app(handler_status: int = 200) -> tuple[FastAPI, dict[str, int]]:
    app = FastAPI()
    app.state.api_idempotency_service = ApiIdempotencyService(_build_session_factory())
    app.middleware("http")(api_idempotency_middleware)
    calls = {"count": 0}

    @app.post("/api/ingest/test")
    async def handler(payload: dict):
        calls["count"] += 1
        if handler_status != 200:
            return JSONResponse(
                status_code=handler_status,
                content={"call": calls["count"], "status": "failed"},
            )
        return {"call": calls["count"], "payload": payload}

    app.add_api_route("/api/supplement/propose", handler, methods=["POST"])

    return app, calls


def test_middleware_replays_same_json_response_and_detects_payload_conflict() -> None:
    app, calls = _build_test_app()
    with TestClient(app) as client:
        first = client.post(
            "/api/ingest/test",
            headers={"Idempotency-Key": "request-3"},
            json={"b": 2, "a": 1},
        )
        replay = client.post(
            "/api/ingest/test",
            headers={"Idempotency-Key": "request-3"},
            json={"a": 1, "b": 2},
        )
        conflict = client.post(
            "/api/ingest/test",
            headers={"Idempotency-Key": "request-3"},
            json={"a": 9, "b": 2},
        )

    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json() == {"call": 1, "payload": {"b": 2, "a": 1}}
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error_code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert calls["count"] == 1


def test_middleware_replays_failed_response() -> None:
    app, calls = _build_test_app(handler_status=422)
    with TestClient(app) as client:
        first = client.post(
            "/api/supplement/propose",
            headers={"Idempotency-Key": "request-4"},
            json={"source_document_id": 1},
        )
        replay = client.post(
            "/api/supplement/propose",
            headers={"Idempotency-Key": "request-4"},
            json={"source_document_id": 1},
        )

    assert first.status_code == replay.status_code == 422
    assert first.json() == replay.json()
    assert calls["count"] == 1
    session = app.state.api_idempotency_service._session_factory()
    try:
        record = session.query(ApiIdempotencyRecord).one()
        assert record.status == API_IDEMPOTENCY_FAILED
    finally:
        session.close()
