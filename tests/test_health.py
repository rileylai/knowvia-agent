import logging

from fastapi.testclient import TestClient

from src.app.main import app


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_logs_workflow_id(caplog) -> None:
    caplog.set_level(logging.INFO, logger="learnloop.request")
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["X-Workflow-ID"]

    records = [
        record
        for record in caplog.records
        if record.name == "learnloop.request"
        and record.getMessage() == "request_completed"
    ]
    assert records

    request_record = records[-1]
    assert request_record.workflow_id == response.headers["X-Workflow-ID"]
    assert request_record.path == "/health"
    assert request_record.method == "GET"
    assert request_record.status_code == 200
