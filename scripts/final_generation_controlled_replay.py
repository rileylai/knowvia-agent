#!/usr/bin/env python3
"""Run a bounded, in-memory A/B replay at the final-generation provider seam."""

from __future__ import annotations

import argparse
import asyncio
from enum import Enum
import json
from pathlib import Path
import sys
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.providers import (  # noqa: E402
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderRouter,
)


FINAL_OPERATION = "bounded_agent_final"
MAX_REPLAY_ATTEMPTS = 3
TARGET_QUERY = (
    "Considering our company size and development preferences,\n"
    "which practices from the indexed PDF should we prioritize?"
)
TRANSFORM_QUERY = "用中文解釋剛剛的回答"
CONTEXT_BEGIN = "[BEGIN UNTRUSTED CONVERSATION_CONTEXT]\n"
CONTEXT_END = "\n[END UNTRUSTED CONVERSATION_CONTEXT]"


class ControlledReplayError(RuntimeError):
    """Fail closed when a replay would not preserve the controlled variables."""


class ReplayOutcome(str, Enum):
    FINAL_TEXT = "final_text"
    INSUFFICIENT_SENTINEL = "insufficient_sentinel"
    MALFORMED = "malformed"
    PROVIDER_ERROR = "provider_error"


class CapturingProvider(LLMProvider):
    """Delegate normally and optionally retain exact final requests in memory."""

    def __init__(self, delegate: LLMProvider) -> None:
        self._delegate = delegate
        self._capture_enabled = False
        self._captured_requests: list[LLMRequest] = []

    @property
    def name(self) -> str:
        return self._delegate.name

    @property
    def supports_tool_calling(self) -> bool:
        return bool(getattr(self._delegate, "supports_tool_calling", False))

    @property
    def supports_structured_output(self) -> bool:
        return bool(getattr(self._delegate, "supports_structured_output", False))

    @property
    def captured_count(self) -> int:
        return len(self._captured_requests)

    def enable_capture(self) -> None:
        self._capture_enabled = True

    def disable_capture(self) -> None:
        self._capture_enabled = False

    def take_latest(self) -> LLMRequest:
        if not self._captured_requests:
            raise ControlledReplayError("no final-generation request was captured")
        latest = self._captured_requests[-1].model_copy(deep=True)
        self._captured_requests.clear()
        return latest

    async def generate(self, request: LLMRequest) -> LLMResponse:
        operation = (request.metadata or {}).get("operation")
        if self._capture_enabled and operation == FINAL_OPERATION:
            self._captured_requests.append(request.model_copy(deep=True))
        return await self._delegate.generate(request)


def _validate_final_request(request: LLMRequest) -> None:
    if (request.metadata or {}).get("operation") != FINAL_OPERATION:
        raise ControlledReplayError("request is not a final-generation request")
    if request.tools is not None or request.tool_choice is not None:
        raise ControlledReplayError("final-generation replay must not expose tools")
    if request.response_format is not None:
        raise ControlledReplayError("final-generation replay must not select structured output")
    if len(request.messages) != 3:
        raise ControlledReplayError("final-generation request shape is not recognized")
    if [message.role for message in request.messages] != ["system", "user", "system"]:
        raise ControlledReplayError("final-generation message roles are not recognized")


def _context_bounds(content: str) -> tuple[int, int, str]:
    if content.count(CONTEXT_BEGIN) != 1 or content.count(CONTEXT_END) != 1:
        raise ControlledReplayError("conversation context block is not uniquely identifiable")
    payload_start = content.index(CONTEXT_BEGIN) + len(CONTEXT_BEGIN)
    payload_end = content.index(CONTEXT_END, payload_start)
    if payload_end <= payload_start:
        raise ControlledReplayError("conversation context block is empty")
    return payload_start, payload_end, content[payload_start:payload_end]


def _escaped_context_value(value: str) -> str:
    end_marker = CONTEXT_END.removeprefix("\n")
    return value.replace(end_marker, f"[ESCAPED {end_marker}]")


def build_without_previous_assistant_request(
    request: LLMRequest,
    *,
    previous_answer: str,
    current_query: str,
) -> LLMRequest:
    """Copy a captured request and remove exactly one selected assistant turn."""

    _validate_final_request(request)
    if not previous_answer or not current_query:
        raise ControlledReplayError("controlled history values must not be empty")
    variant = request.model_copy(deep=True)
    content = variant.messages[1].content
    payload_start, payload_end, payload = _context_bounds(content)
    escaped_answer = _escaped_context_value(previous_answer)
    escaped_query = _escaped_context_value(current_query)
    target = f"\n\n[assistant] {escaped_answer}\n\n[user] {escaped_query}"
    replacement = f"\n\n[user] {escaped_query}"
    if payload.count(target) != 1:
        raise ControlledReplayError(
            "previous successful assistant turn is not uniquely identifiable"
        )
    updated_payload = payload.replace(target, replacement, 1)
    variant.messages[1].content = (
        content[:payload_start] + updated_payload + content[payload_end:]
    )
    return variant


def build_fresh_authority_request(
    request: LLMRequest,
    *,
    current_query: str,
) -> LLMRequest:
    """Copy a captured request and remove its complete conversation-history block."""

    _validate_final_request(request)
    if not current_query:
        raise ControlledReplayError("current query must not be empty")
    variant = request.model_copy(deep=True)
    content = variant.messages[1].content
    payload_start, payload_end, _ = _context_bounds(content)
    block_start = payload_start - len(CONTEXT_BEGIN)
    if content[max(0, block_start - 2) : block_start] != "\n\n":
        raise ControlledReplayError("conversation context separator is not recognized")
    block_start -= 2
    block_end = payload_end + len(CONTEXT_END)
    variant.messages[1].content = content[:block_start] + content[block_end:]
    expected_suffix = f"\n{_escaped_context_value(current_query)}\n[END UNTRUSTED USER_MESSAGE]"
    if expected_suffix not in variant.messages[1].content:
        raise ControlledReplayError("current user message was not preserved")
    return variant


def _history_entries(request: LLMRequest) -> list[str]:
    _validate_final_request(request)
    _, _, payload = _context_bounds(request.messages[1].content)
    entries = payload.split("\n\n")
    if not entries or any(not entry.startswith("[user] ") for entry in entries):
        raise ControlledReplayError("user-side conversation history is not recognized")
    return [entry.removeprefix("[user] ") for entry in entries]


def _replace_history_entries(
    request: LLMRequest,
    *,
    entries: list[str],
) -> LLMRequest:
    if not entries:
        raise ControlledReplayError("conversation history cannot be empty")
    variant = request.model_copy(deep=True)
    content = variant.messages[1].content
    payload_start, payload_end, _ = _context_bounds(content)
    replacement = "\n\n".join(f"[user] {entry}" for entry in entries)
    variant.messages[1].content = (
        content[:payload_start] + replacement + content[payload_end:]
    )
    return variant


def build_without_prior_substantive_user_requests(
    request: LLMRequest,
    *,
    current_query: str,
) -> tuple[LLMRequest, int]:
    """Remove exact prior substantive user entries while keeping the current one."""

    escaped_query = _escaped_context_value(current_query)
    entries = _history_entries(request)
    matching_indexes = [
        index for index, entry in enumerate(entries) if entry == escaped_query
    ]
    if len(matching_indexes) < 2 or matching_indexes[-1] != len(entries) - 1:
        raise ControlledReplayError(
            "prior substantive user messages are not uniquely and safely identifiable"
        )
    keep_current_index = matching_indexes[-1]
    retained = [
        entry
        for index, entry in enumerate(entries)
        if entry != escaped_query or index == keep_current_index
    ]
    return _replace_history_entries(request, entries=retained), len(matching_indexes) - 1


def build_without_transform_user_requests(
    request: LLMRequest,
    *,
    transform_query: str,
) -> tuple[Optional[LLMRequest], int]:
    """Remove exact known transform-only user entries, or report not applicable."""

    escaped_transform = _escaped_context_value(transform_query)
    entries = _history_entries(request)
    removed_count = sum(entry == escaped_transform for entry in entries)
    if removed_count == 0:
        return None, 0
    retained = [entry for entry in entries if entry != escaped_transform]
    return _replace_history_entries(request, entries=retained), removed_count


def describe_user_history(
    request: LLMRequest,
    *,
    current_query: str,
    transform_query: str,
) -> dict[str, object]:
    """Return bounded categories without exposing conversation text."""

    escaped_query = _escaped_context_value(current_query)
    escaped_transform = _escaped_context_value(transform_query)
    entries = _history_entries(request)
    categories = [
        "substantive"
        if entry == escaped_query
        else "transform"
        if entry == escaped_transform
        else "other"
        for entry in entries
    ]
    return {
        "message_count": len(entries),
        "role_sequence": ["user"] * len(entries),
        "previous_user_categories": categories[:-1],
    }


def _classify_response(response: LLMResponse) -> ReplayOutcome:
    if response.tool_calls:
        return ReplayOutcome.MALFORMED
    output = (response.output_text or "").strip()
    if not output:
        return ReplayOutcome.MALFORMED
    if output.casefold().strip(" .!`\"") == "insufficient_info":
        return ReplayOutcome.INSUFFICIENT_SENTINEL
    return ReplayOutcome.FINAL_TEXT


async def replay_request(
    provider: LLMProvider,
    request: LLMRequest,
    *,
    attempts: int,
) -> list[ReplayOutcome]:
    """Replay one exact final request without entering Agent or tool execution."""

    _validate_final_request(request)
    if attempts < 1 or attempts > MAX_REPLAY_ATTEMPTS:
        raise ControlledReplayError(
            f"attempts must be between 1 and {MAX_REPLAY_ATTEMPTS}"
        )
    outcomes: list[ReplayOutcome] = []
    for _ in range(attempts):
        try:
            response = await provider.generate(request.model_copy(deep=True))
        except Exception:
            outcomes.append(ReplayOutcome.PROVIDER_ERROR)
        else:
            outcomes.append(_classify_response(response))
    return outcomes


def _request_headers(api_token: Optional[str]) -> dict[str, str]:
    if not api_token:
        return {}
    return {"Authorization": f"Bearer {api_token}"}


async def _run_replays(
    *,
    provider: LLMProvider,
    exact_request: LLMRequest,
    attempts: int,
) -> dict[str, object]:
    variant_b, removed_substantive_count = (
        build_without_prior_substantive_user_requests(
            exact_request,
            current_query=TARGET_QUERY,
        )
    )
    variant_c, removed_transform_count = build_without_transform_user_requests(
        exact_request,
        transform_query=TRANSFORM_QUERY,
    )
    variant_d = build_fresh_authority_request(
        exact_request,
        current_query=TARGET_QUERY,
    )
    a = await replay_request(provider, exact_request, attempts=attempts)
    b = await replay_request(provider, variant_b, attempts=attempts)
    c = (
        await replay_request(provider, variant_c, attempts=attempts)
        if variant_c is not None
        else None
    )
    d = await replay_request(provider, variant_d, attempts=attempts)
    return {
        "variant_a": [outcome.value for outcome in a],
        "variant_b": {
            "removed_previous_substantive_user_count": removed_substantive_count,
            "outcomes": [outcome.value for outcome in b],
        },
        "variant_c": (
            {
                "removed_transform_user_count": removed_transform_count,
                "outcomes": [outcome.value for outcome in c],
            }
            if c is not None
            else {
                "removed_transform_user_count": 0,
                "outcomes": "not_applicable",
            }
        ),
        "variant_d": {
            "removed_prior_history_count": len(_history_entries(exact_request)) - 1,
            "outcomes": [outcome.value for outcome in d],
        },
        "controlled_variable_verified": True,
        "retrieval_reexecuted_during_replay": False,
    }


def run_live_experiment(*, attempts: int, include_transform: bool) -> dict[str, object]:
    """Create a fresh conversation, capture its repeated final request, and replay it."""

    if attempts < 1 or attempts > MAX_REPLAY_ATTEMPTS:
        raise ControlledReplayError(
            f"attempts must be between 1 and {MAX_REPLAY_ATTEMPTS}"
        )

    from fastapi.testclient import TestClient

    from src.app.dependencies import get_provider_router
    from src.app.main import app, settings

    production_router = get_provider_router()
    delegate = production_router.get_provider("openai")
    capturing_provider = CapturingProvider(delegate)
    diagnostic_router = ProviderRouter()
    diagnostic_router.register_provider(capturing_provider)
    app.dependency_overrides[get_provider_router] = lambda: diagnostic_router
    headers = _request_headers(settings.api_bearer_token)

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            created = client.post("/api/conversations", headers=headers)
            if created.status_code != 201:
                raise ControlledReplayError("fresh conversation could not be created")
            session_id = int(created.json()["id"])
            payload = {
                "query": TARGET_QUERY,
                "top_k": 10,
                "provider_name": "openai",
                "model": "gpt-4o-mini",
            }
            first = client.post(
                f"/api/conversations/{session_id}/messages",
                headers=headers,
                json=payload,
            )
            if first.status_code != 200:
                raise ControlledReplayError("first mixed request did not complete")
            first_payload = first.json()
            if not first_payload.get("citations") or not first_payload.get(
                "used_saved_memory"
            ):
                raise ControlledReplayError(
                    "first mixed request did not acquire both required authorities"
                )
            if not str(first_payload.get("answer") or ""):
                raise ControlledReplayError("first mixed answer is unavailable")

            second = client.post(
                f"/api/conversations/{session_id}/messages",
                headers=headers,
                json=payload,
            )
            if second.status_code != 200:
                raise ControlledReplayError("second mixed request did not complete")

            transform_status = "not_applicable"
            expected_user_message_count = 2
            if include_transform:
                transform = client.post(
                    f"/api/conversations/{session_id}/messages",
                    headers=headers,
                    json={**payload, "query": TRANSFORM_QUERY},
                )
                if transform.status_code != 200:
                    raise ControlledReplayError("conversation transform did not complete")
                transform_status = "completed"
                expected_user_message_count += 1

            capturing_provider.enable_capture()
            repeated = None
            repeated_attempts = 0
            for _ in range(4):
                repeated_attempts += 1
                expected_user_message_count += 1
                repeated = client.post(
                    f"/api/conversations/{session_id}/messages",
                    headers=headers,
                    json=payload,
                )
                if repeated.status_code != 200:
                    break
            capturing_provider.disable_capture()
            if repeated is None or repeated.status_code == 200:
                raise ControlledReplayError(
                    "failed repeated mixed request did not reproduce within four attempts"
                )
            exact_request = capturing_provider.take_latest()

            reloaded = client.get(
                f"/api/conversations/{session_id}",
                headers=headers,
            )
            if reloaded.status_code != 200:
                raise ControlledReplayError("conversation reload failed")
            persisted_user_count = sum(
                message.get("role") == "user"
                for message in reloaded.json().get("messages", [])
            )

        from src.db.models import WorkflowRun
        from src.db.session import get_db_session_factory

        session_factory = get_db_session_factory()
        db_session = session_factory()
        try:
            workflow = (
                db_session.query(WorkflowRun)
                .filter(WorkflowRun.workflow_type == "agent")
                .order_by(WorkflowRun.id.desc())
                .first()
            )
            workflow_metadata = json.loads(workflow.metadata_json or "{}") if workflow else {}
        finally:
            db_session.close()

        replay_results = asyncio.run(
            _run_replays(
                provider=delegate,
                exact_request=exact_request,
                attempts=attempts,
            )
        )
        replay_results.update(
            {
                "session_id": session_id,
                "first_request": "completed",
                "second_request": "completed",
                "transform_request": transform_status,
                "failed_repeated_attempt": repeated_attempts,
                "repeated_request": "provider_error",
                "captured_final_request": True,
                "snapshot_persisted": False,
                "current_request_history": describe_user_history(
                    exact_request,
                    current_query=TARGET_QUERY,
                    transform_query=TRANSFORM_QUERY,
                ),
                "fresh_authority": {
                    "knowledge_accepted_count": workflow_metadata.get(
                        "knowledge_accepted_evidence_count"
                    ),
                    "memory_hit_aggregate": workflow_metadata.get(
                        "memory_retrieval_hit_count"
                    ),
                    "tool_calls_used": workflow_metadata.get("tool_calls_used"),
                },
                "failed_user_message_persistence": {
                    "expected_user_message_count": expected_user_message_count,
                    "persisted_user_message_count": persisted_user_count,
                    "visible_after_reload": (
                        persisted_user_count == expected_user_message_count
                    ),
                },
            }
        )
        return replay_results
    finally:
        app.dependency_overrides.pop(get_provider_router, None)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a bounded in-memory final-generation A/B replay."
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=3,
        choices=range(1, MAX_REPLAY_ATTEMPTS + 1),
    )
    parser.add_argument(
        "--include-transform",
        action="store_true",
        help="Insert the known transform-only turn before capturing a failed repeat.",
    )
    args = parser.parse_args()
    try:
        report = run_live_experiment(
            attempts=args.attempts,
            include_transform=args.include_transform,
        )
    except ControlledReplayError as exc:
        print(json.dumps({"status": "infeasible", "reason": str(exc)}))
        return 2
    print(json.dumps({"status": "completed", **report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
