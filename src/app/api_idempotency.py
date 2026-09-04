from __future__ import annotations

import hashlib
import json
import re
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from src.services import (
    ApiIdempotencyClaim,
    ApiIdempotencyConflictError,
    ApiIdempotencyService,
    ApiIdempotencyStoreError,
)


IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
IDEMPOTENCY_PATH_PREFIXES = ("/api/ingest/", "/api/supplement/")
_BOUNDARY_PATTERN = re.compile(r'boundary="?([^";]+)')


def is_idempotent_api_mutation(request: Request) -> bool:
    return request.method == "POST" and request.url.path.startswith(
        IDEMPOTENCY_PATH_PREFIXES
    )


async def api_idempotency_middleware(request: Request, call_next):
    if not is_idempotent_api_mutation(request):
        return await call_next(request)

    raw_key = request.headers.get(IDEMPOTENCY_KEY_HEADER)
    if raw_key is None or not raw_key.strip():
        return await call_next(request)
    idempotency_key = raw_key.strip()
    if len(idempotency_key) > 255:
        return _error_response(
            status_code=400,
            error_code="IDEMPOTENCY_KEY_INVALID",
            message="Idempotency-Key must be at most 255 characters",
            failure_reason="IDEMPOTENCY_KEY_CONFLICT",
        )

    service = getattr(request.app.state, "api_idempotency_service", None)
    if not isinstance(service, ApiIdempotencyService):
        return _error_response(
            status_code=503,
            error_code="IDEMPOTENCY_STORE_FAILED",
            message="API idempotency store is not configured",
            failure_reason="IDEMPOTENCY_STORE_FAILED",
        )

    body = await request.body()
    claim = _claim_request(
        service,
        request_scope=f"{request.method}:{request.url.path}",
        idempotency_key=idempotency_key,
        request_fingerprint=_fingerprint(request, body),
    )
    if isinstance(claim, Response):
        return claim
    if not claim.owner:
        return _replay_claim(claim)

    try:
        response = await call_next(request)
        body_bytes = await _read_response_body(response)
    except Exception:
        _complete_failed_request(
            service,
            claim,
            response_status_code=500,
            response_body=json.dumps(
                {
                    "detail": {
                        "error_code": "API_MUTATION_FAILED",
                        "message": "API mutation failed",
                        "failure_reason": "UNKNOWN_ERROR",
                    }
                }
            ),
        )
        raise

    headers = _replayable_headers(response.headers)
    try:
        service.complete(
            claim,
            response_status_code=response.status_code,
            response_body=body_bytes.decode("utf-8"),
            response_headers=headers,
        )
    except ApiIdempotencyStoreError as exc:
        raise RuntimeError("API idempotency response could not be persisted") from exc

    return Response(
        content=body_bytes,
        status_code=response.status_code,
        headers=dict(response.headers),
        media_type=response.media_type,
        background=response.background,
    )


def _claim_request(
    service: ApiIdempotencyService,
    *,
    request_scope: str,
    idempotency_key: str,
    request_fingerprint: str,
):
    try:
        return service.claim(
            request_scope=request_scope,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
    except ApiIdempotencyConflictError:
        return _error_response(
            status_code=409,
            error_code="IDEMPOTENCY_KEY_CONFLICT",
            message="Idempotency-Key was reused with a different request payload",
            failure_reason="IDEMPOTENCY_KEY_CONFLICT",
        )
    except ApiIdempotencyStoreError:
        return _error_response(
            status_code=503,
            error_code="IDEMPOTENCY_STORE_FAILED",
            message="API idempotency store is unavailable",
            failure_reason="IDEMPOTENCY_STORE_FAILED",
        )


def _replay_claim(claim: ApiIdempotencyClaim) -> Response:
    if claim.status == "running":
        return _error_response(
            status_code=202,
            error_code="IDEMPOTENCY_IN_PROGRESS",
            message="A request with this Idempotency-Key is already running",
            failure_reason="IDEMPOTENCY_IN_PROGRESS",
        )
    if claim.response_status_code is None or claim.response_body is None:
        return _error_response(
            status_code=503,
            error_code="IDEMPOTENCY_STORE_FAILED",
            message="Persisted API idempotency response is missing",
            failure_reason="IDEMPOTENCY_STORE_FAILED",
        )
    headers = {}
    if claim.response_headers_json:
        try:
            parsed_headers = json.loads(claim.response_headers_json)
            if isinstance(parsed_headers, dict):
                headers = {str(key): str(value) for key, value in parsed_headers.items()}
        except (TypeError, ValueError):
            headers = {}
    return Response(
        content=claim.response_body.encode("utf-8"),
        status_code=claim.response_status_code,
        headers=headers,
        media_type=headers.get("content-type"),
    )


def _fingerprint(request: Request, body: bytes) -> str:
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    normalized_body = body
    if media_type == "application/json":
        try:
            normalized_body = json.dumps(
                json.loads(body.decode("utf-8")),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
        except (UnicodeDecodeError, TypeError, ValueError):
            normalized_body = body
    elif media_type == "multipart/form-data":
        match = _BOUNDARY_PATTERN.search(content_type)
        if match:
            normalized_body = body.replace(
                match.group(1).encode("utf-8"),
                b"<normalized-boundary>",
            )
    fingerprint_input = media_type.encode("utf-8") + b"\n" + normalized_body
    return hashlib.sha256(fingerprint_input).hexdigest()


async def _read_response_body(response) -> bytes:
    body = bytearray()
    async for chunk in response.body_iterator:
        body.extend(chunk)
    return bytes(body)


def _replayable_headers(headers) -> dict[str, str]:
    return {
        name: value
        for name, value in headers.items()
        if name.lower() in {"content-type", "x-workflow-id", "location"}
    }


def _complete_failed_request(
    service: ApiIdempotencyService,
    claim: ApiIdempotencyClaim,
    *,
    response_status_code: int,
    response_body: str,
) -> None:
    try:
        service.complete(
            claim,
            response_status_code=response_status_code,
            response_body=response_body,
            response_headers={"content-type": "application/json"},
        )
    except ApiIdempotencyStoreError:
        pass


def _error_response(
    *,
    status_code: int,
    error_code: str,
    message: str,
    failure_reason: str,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "detail": {
                "error_code": error_code,
                "message": message,
                "failure_reason": failure_reason,
                "workflow_run_id": None,
            }
        },
    )
