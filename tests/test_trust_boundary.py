from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from src.app.config import get_settings
from src.app.legacy import app
from src.services import TrustBoundaryError, TrustBoundaryService


def test_api_bearer_caller_matrix() -> None:
    boundary = TrustBoundaryService(api_bearer_token="api-secret")

    with pytest.raises(TrustBoundaryError) as missing:
        boundary.require_api_bearer(None)
    assert missing.value.http_status_code == 401
    assert missing.value.failure_reason == "AUTHENTICATION_FAILED"

    with pytest.raises(TrustBoundaryError) as wrong:
        boundary.require_api_bearer("Bearer wrong-secret")
    assert wrong.value.http_status_code == 401

    boundary.require_api_bearer("Bearer api-secret")


def test_telegram_webhook_secret_rejects_forged_call() -> None:
    boundary = TrustBoundaryService(telegram_webhook_secret="webhook-secret")

    with pytest.raises(TrustBoundaryError) as forged:
        boundary.require_telegram_webhook_secret("forged-secret")
    assert forged.value.http_status_code == 403
    assert forged.value.error_code == "TELEGRAM_WEBHOOK_FORBIDDEN"

    with pytest.raises(TrustBoundaryError):
        boundary.require_telegram_webhook_secret(None)

    boundary.require_telegram_webhook_secret("webhook-secret")


def test_telegram_allowed_chat_matrix() -> None:
    boundary = TrustBoundaryService(telegram_allowed_chat_ids=frozenset({"555"}))

    with pytest.raises(TrustBoundaryError) as denied:
        boundary.require_allowed_telegram_chat("777")
    assert denied.value.http_status_code == 403
    assert denied.value.error_code == "TELEGRAM_CHAT_NOT_ALLOWED"

    boundary.require_allowed_telegram_chat("555")


def test_telegram_webhook_route_rejects_forged_secret(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "webhook-secret")
    get_settings.cache_clear()

    try:
        response = TestClient(app).post(
            "/api/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "forged-secret"},
            json={
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 555},
                    "text": "/help",
                },
            },
        )
    finally:
        monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
        get_settings.cache_clear()

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "TELEGRAM_WEBHOOK_FORBIDDEN"
