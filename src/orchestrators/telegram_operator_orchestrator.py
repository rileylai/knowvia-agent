from __future__ import annotations

import shlex
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Optional

from src.orchestrators.supplement_query_orchestrator import (
    SupplementQueryError,
    SupplementQueryOrchestrator,
    SupplementReviewItemResult,
)
from src.observability.redaction import sanitize_sensitive_text
from src.services import (
    COST_SCOPE_7D,
    COST_SCOPE_MONTH,
    COST_SCOPE_TODAY,
    COST_SCOPE_WORKFLOW,
    CostScopeSnapshot,
    KnowledgeStatsResult,
    KnowledgeStatsService,
    ReadinessStatusReport,
    ReadinessService,
    WorkflowObservabilityService,
)
from src.services.workflow_observability import WorkflowStatusView


@dataclass(frozen=True)
class TelegramCostResult:
    status: str
    reply_text: str
    scope: str
    workflow_run_id: Optional[int]
    total_cost_usd: float
    llm_cost_usd: float
    embedding_cost_usd: float
    unknown_cost_workflow_count: int
    budget_status: str
    budget_usd: Optional[float]
    workflow_budget_exceeded_count: int
    workflow_budget_usd: Optional[float]


@dataclass(frozen=True)
class TelegramWorkflowResult:
    status: str
    reply_text: str
    workflow_run_id: Optional[int]
    workflow_type: Optional[str]
    workflow_status: Optional[str]
    failure_reason: Optional[str]
    age_seconds: Optional[float]
    stale: Optional[bool]
    estimated_cost_usd: Optional[float]
    recent_workflow_count: int


@dataclass(frozen=True)
class TelegramPendingItem:
    change_request_id: int
    title: str
    summary: str
    source_display_name: str
    target_path: str
    concepts: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class TelegramPendingResult:
    status: str
    reply_text: str
    items: tuple[TelegramPendingItem, ...]


@dataclass(frozen=True)
class TelegramStatusCheck:
    name: str
    status: str
    failure_reason: Optional[str]


@dataclass(frozen=True)
class TelegramStatusResult:
    status: str
    reply_text: str
    liveness_status: str
    readiness_status: str
    checks: tuple[TelegramStatusCheck, ...]


@dataclass(frozen=True)
class TelegramStatsResult:
    status: str
    reply_text: str
    page_count: int
    block_count: int
    chunk_count: int
    vector_count: int
    proposal_count: int
    pending_proposal_count: int
    accepted_proposal_count: int
    rejected_proposal_count: int
    latest_successful_full_index_at: Optional[str]
    latest_successful_incremental_sync_at: Optional[str]


class TelegramOperatorError(Exception):
    def __init__(
        self,
        *,
        error_code: str,
        message: str,
        http_status_code: int,
        failure_reason: str = "UNKNOWN_ERROR",
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status_code = http_status_code
        self.failure_reason = failure_reason


class TelegramOperatorOrchestrator:
    """Expose bounded, read-only Telegram operator views."""

    _RECENT_WORKFLOW_LIMIT = 5
    _PENDING_LIMIT = 8
    _PENDING_TITLE_LIMIT = 120
    _PENDING_SUMMARY_LIMIT = 260
    _PENDING_DISPLAY_LIMIT = 160
    _SAFE_METADATA_KEYS = (
        "operation",
        "sync_mode",
        "discovered_page_count",
        "processed_page_count",
        "failed_page_count",
        "remaining_page_count",
        "indexed_block_count",
        "indexed_chunk_count",
        "retrieved_chunk_count",
        "citation_count",
        "change_request_status",
        "business_status",
    )

    def __init__(
        self,
        *,
        workflow_observability_service: WorkflowObservabilityService,
        supplement_query_orchestrator: Optional[SupplementQueryOrchestrator] = None,
        readiness_service: Optional[ReadinessService] = None,
        knowledge_stats_service: Optional[KnowledgeStatsService] = None,
    ) -> None:
        self._workflow_observability_service = workflow_observability_service
        self._supplement_query_orchestrator = supplement_query_orchestrator
        self._readiness_service = readiness_service
        self._knowledge_stats_service = knowledge_stats_service

    def get_status(self, *, command_text: str) -> TelegramStatusResult:
        self._require_exact_command(command_text, usage="/status")
        if self._readiness_service is None:
            raise TelegramOperatorError(
                error_code="TELEGRAM_OPERATOR_NOT_CONFIGURED",
                message="Telegram readiness status is not configured",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        report = self._readiness_service.status()
        checks = tuple(
            TelegramStatusCheck(
                name=name,
                status=check.status,
                failure_reason=check.failure_reason,
            )
            for name, check in report.checks.items()
        )
        return TelegramStatusResult(
            status="ready" if report.is_ready else "not_ready",
            reply_text=self._status_reply(report),
            liveness_status=report.liveness.status,
            readiness_status="ready" if report.is_ready else "not_ready",
            checks=checks,
        )

    def get_stats(self, *, command_text: str) -> TelegramStatsResult:
        self._require_exact_command(command_text, usage="/stats")
        if self._knowledge_stats_service is None:
            raise TelegramOperatorError(
                error_code="TELEGRAM_OPERATOR_NOT_CONFIGURED",
                message="Telegram knowledge statistics are not configured",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        snapshot = self._knowledge_stats_service.snapshot()
        return TelegramStatsResult(
            status="succeeded",
            reply_text=self._stats_reply(snapshot),
            page_count=snapshot.page_count,
            block_count=snapshot.block_count,
            chunk_count=snapshot.chunk_count,
            vector_count=snapshot.vector_count,
            proposal_count=snapshot.proposal_count,
            pending_proposal_count=snapshot.pending_proposal_count,
            accepted_proposal_count=snapshot.accepted_proposal_count,
            rejected_proposal_count=snapshot.rejected_proposal_count,
            latest_successful_full_index_at=self._safe_timestamp(
                snapshot.latest_successful_full_index_at
            ),
            latest_successful_incremental_sync_at=self._safe_timestamp(
                snapshot.latest_successful_incremental_sync_at
            ),
        )

    def get_pending(self, *, command_text: str) -> TelegramPendingResult:
        tokens = self._parse_tokens(command_text, usage="/pending")
        if len(tokens) != 1:
            raise TelegramOperatorError(
                error_code="INVALID_ARGUMENT",
                message="Usage: /pending",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="INVALID_ARGUMENT",
            )
        if self._supplement_query_orchestrator is None:
            raise TelegramOperatorError(
                error_code="TELEGRAM_OPERATOR_NOT_CONFIGURED",
                message="Telegram pending review is not configured",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        try:
            items = self._supplement_query_orchestrator.list_pending(
                limit=self._PENDING_LIMIT
            )
        except SupplementQueryError as exc:
            raise TelegramOperatorError(
                error_code=exc.error_code,
                message="Pending proposals could not be loaded.",
                http_status_code=exc.http_status_code,
                failure_reason=exc.failure_reason,
            ) from exc
        pending_items = tuple(self._pending_item(item) for item in items)
        return TelegramPendingResult(
            status="succeeded",
            reply_text=self._pending_list_reply(pending_items),
            items=pending_items,
        )

    def get_pending_detail(self, *, change_request_id: int) -> TelegramPendingResult:
        if change_request_id <= 0:
            raise TelegramOperatorError(
                error_code="INVALID_ARGUMENT",
                message="change_request_id must be positive",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="INVALID_ARGUMENT",
            )
        if self._supplement_query_orchestrator is None:
            raise TelegramOperatorError(
                error_code="TELEGRAM_OPERATOR_NOT_CONFIGURED",
                message="Telegram pending review is not configured",
                http_status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
                failure_reason="UNKNOWN_ERROR",
            )
        try:
            item = self._supplement_query_orchestrator.get_detail(
                change_request_id=change_request_id
            )
        except SupplementQueryError as exc:
            raise TelegramOperatorError(
                error_code=exc.error_code,
                message="Pending proposal detail could not be loaded.",
                http_status_code=exc.http_status_code,
                failure_reason=exc.failure_reason,
            ) from exc
        if item.status.strip().lower() != "pending":
            raise TelegramOperatorError(
                error_code="INVALID_STATE_TRANSITION",
                message="This proposal is no longer pending.",
                http_status_code=HTTPStatus.CONFLICT,
                failure_reason="UNKNOWN_ERROR",
            )
        pending_item = self._pending_item(item)
        return TelegramPendingResult(
            status="succeeded",
            reply_text=self._pending_detail_reply(pending_item),
            items=(pending_item,),
        )

    def get_cost(self, *, command_text: str) -> TelegramCostResult:
        scope, workflow_run_id = self._parse_cost_command(command_text)
        summary = self._workflow_observability_service.cost_summary(
            scope=scope,
            workflow_run_id=workflow_run_id,
            limit=10000,
        )
        if summary is None:
            raise TelegramOperatorError(
                error_code="WORKFLOW_NOT_FOUND",
                message="The requested workflow cost was not found.",
                http_status_code=HTTPStatus.NOT_FOUND,
                failure_reason="UNKNOWN_ERROR",
            )
        return self._cost_result(summary)

    def get_workflow(self, *, command_text: str) -> TelegramWorkflowResult:
        workflow_run_id = self._parse_workflow_command(command_text)
        if workflow_run_id is None:
            workflows = self._workflow_observability_service.list_workflows(
                limit=self._RECENT_WORKFLOW_LIMIT
            )
            return TelegramWorkflowResult(
                status="succeeded",
                reply_text=self._recent_workflow_reply(workflows),
                workflow_run_id=None,
                workflow_type=None,
                workflow_status=None,
                failure_reason=None,
                age_seconds=None,
                stale=None,
                estimated_cost_usd=None,
                recent_workflow_count=len(workflows),
            )

        workflow = self._workflow_observability_service.get_workflow(workflow_run_id)
        if workflow is None:
            raise TelegramOperatorError(
                error_code="WORKFLOW_NOT_FOUND",
                message="The requested workflow was not found.",
                http_status_code=HTTPStatus.NOT_FOUND,
                failure_reason="UNKNOWN_ERROR",
            )
        return TelegramWorkflowResult(
            status=workflow.status,
            reply_text=self._workflow_detail_reply(workflow),
            workflow_run_id=workflow.workflow_run_id,
            workflow_type=self._safe_label(workflow.workflow_type),
            workflow_status=self._safe_label(workflow.status),
            failure_reason=self._safe_failure_reason(workflow.failure_reason),
            age_seconds=workflow.age_seconds,
            stale=workflow.stale,
            estimated_cost_usd=workflow.estimated_cost_usd,
            recent_workflow_count=1,
        )

    @classmethod
    def _pending_item(
        cls,
        item: SupplementReviewItemResult,
    ) -> TelegramPendingItem:
        target_path = (
            item.target_page.notion_path
            if item.target_page is not None
            else "unassigned"
        )
        return TelegramPendingItem(
            change_request_id=item.change_request_id,
            title=cls._bounded_text(item.proposal.title, cls._PENDING_TITLE_LIMIT),
            summary=cls._bounded_text(item.proposal.summary, cls._PENDING_SUMMARY_LIMIT),
            source_display_name=cls._bounded_text(
                item.proposal.source_display_name,
                cls._PENDING_DISPLAY_LIMIT,
            ),
            target_path=cls._bounded_text(target_path, cls._PENDING_DISPLAY_LIMIT),
            concepts=tuple(
                cls._bounded_text(concept, cls._PENDING_DISPLAY_LIMIT)
                for concept in item.proposal.concepts[:12]
            ),
            notes=tuple(
                cls._bounded_text(note, cls._PENDING_SUMMARY_LIMIT)
                for note in item.proposal.notes[:12]
            ),
        )

    @classmethod
    def _pending_list_reply(cls, items: tuple[TelegramPendingItem, ...]) -> str:
        if not items:
            return "No pending proposals."
        lines = [f"Pending proposals ({len(items)}):"]
        for item in items:
            lines.extend(
                [
                    f"\n#{item.change_request_id} {item.title}",
                    f"Summary: {item.summary}",
                    f"Source: {item.source_display_name}",
                    f"Target: {item.target_path}",
                ]
            )
        return cls._truncate_reply("\n".join(lines))

    @classmethod
    def _pending_detail_reply(cls, item: TelegramPendingItem) -> str:
        lines = [
            f"Pending proposal #{item.change_request_id}",
            f"Title: {item.title}",
            f"Summary: {item.summary}",
            f"Source: {item.source_display_name}",
            f"Target: {item.target_path}",
        ]
        if item.concepts:
            lines.append("Concepts: " + ", ".join(item.concepts))
        if item.notes:
            lines.append("Notes:")
            lines.extend(f"- {note}" for note in item.notes)
        lines.append("Status: pending")
        return cls._truncate_reply("\n".join(lines))

    @classmethod
    def _status_reply(cls, report: ReadinessStatusReport) -> str:
        lines = [
            "LearnLoop Agent status",
            f"Liveness: {report.liveness.status}",
            f"Readiness: {'ready' if report.is_ready else 'not_ready'}",
            "Checks:",
        ]
        for name, check in report.checks.items():
            suffix = f" ({check.failure_reason})" if check.failure_reason else ""
            lines.append(f"- {name}: {check.status}{suffix}")
        return cls._truncate_reply("\n".join(lines))

    @classmethod
    def _stats_reply(cls, snapshot: KnowledgeStatsResult) -> str:
        lines = [
            "LearnLoop Agent stats",
            f"Pages: {snapshot.page_count}",
            f"Blocks: {snapshot.block_count}",
            f"Chunks: {snapshot.chunk_count}",
            f"Vectors: {snapshot.vector_count}",
            f"Proposals: {snapshot.proposal_count}",
            f"Pending proposals: {snapshot.pending_proposal_count}",
            f"Accepted proposals: {snapshot.accepted_proposal_count}",
            f"Rejected proposals: {snapshot.rejected_proposal_count}",
            "Latest full index: "
            + (cls._safe_timestamp(snapshot.latest_successful_full_index_at) or "never"),
            "Latest incremental sync: "
            + (
                cls._safe_timestamp(snapshot.latest_successful_incremental_sync_at)
                or "never"
            ),
        ]
        return cls._truncate_reply("\n".join(lines))

    @staticmethod
    def _safe_timestamp(value: object) -> Optional[str]:
        if value is None:
            return None
        isoformat = getattr(value, "isoformat", None)
        if not callable(isoformat):
            return None
        rendered = str(isoformat())
        return rendered.replace("+00:00", "Z")

    @classmethod
    def _require_exact_command(cls, command_text: str, *, usage: str) -> None:
        tokens = cls._parse_tokens(command_text, usage=usage)
        if len(tokens) != 1:
            raise TelegramOperatorError(
                error_code="INVALID_ARGUMENT",
                message=f"Usage: {usage}",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="INVALID_ARGUMENT",
            )

    @staticmethod
    def _bounded_text(value: object, limit: int) -> str:
        normalized = " ".join(sanitize_sensitive_text(str(value)).split())
        if not normalized:
            return "unknown"
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 1].rstrip() + "…"

    @staticmethod
    def _truncate_reply(value: str) -> str:
        limit = 4096
        if len(value) <= limit:
            return value
        suffix = "\n…"
        return value[: limit - len(suffix)].rstrip() + suffix

    @classmethod
    def _parse_cost_command(cls, command_text: str) -> tuple[str, Optional[int]]:
        tokens = cls._parse_tokens(command_text, usage="/cost [today|7d|month|workflow <workflow_id>]")
        if len(tokens) == 1:
            return COST_SCOPE_TODAY, None
        if len(tokens) == 2 and tokens[1] in {
            COST_SCOPE_TODAY,
            COST_SCOPE_7D,
            COST_SCOPE_MONTH,
        }:
            return tokens[1], None
        if len(tokens) == 3 and tokens[1] == COST_SCOPE_WORKFLOW:
            return COST_SCOPE_WORKFLOW, cls._parse_workflow_id(tokens[2])
        raise TelegramOperatorError(
            error_code="INVALID_ARGUMENT",
            message="Usage: /cost [today|7d|month|workflow <workflow_id>]",
            http_status_code=HTTPStatus.BAD_REQUEST,
            failure_reason="UNKNOWN_ERROR",
        )

    @classmethod
    def _parse_workflow_command(cls, command_text: str) -> Optional[int]:
        tokens = cls._parse_tokens(command_text, usage="/workflow [workflow_id]")
        if len(tokens) == 1:
            return None
        if len(tokens) == 2:
            return cls._parse_workflow_id(tokens[1])
        raise TelegramOperatorError(
            error_code="INVALID_ARGUMENT",
            message="Usage: /workflow [workflow_id]",
            http_status_code=HTTPStatus.BAD_REQUEST,
            failure_reason="UNKNOWN_ERROR",
        )

    @staticmethod
    def _parse_tokens(command_text: str, *, usage: str) -> list[str]:
        try:
            tokens = shlex.split(command_text)
        except ValueError as exc:
            raise TelegramOperatorError(
                error_code="INVALID_ARGUMENT",
                message=f"Usage: {usage}",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="UNKNOWN_ERROR",
            ) from exc
        if not tokens or tokens[0].lstrip("/").lower() != usage.split()[0].lstrip("/"):
            raise TelegramOperatorError(
                error_code="INVALID_ARGUMENT",
                message=f"Usage: {usage}",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="UNKNOWN_ERROR",
            )
        return tokens

    @staticmethod
    def _parse_workflow_id(value: str) -> int:
        try:
            workflow_run_id = int(value)
        except (TypeError, ValueError) as exc:
            raise TelegramOperatorError(
                error_code="INVALID_ARGUMENT",
                message="workflow_id must be a positive integer",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="UNKNOWN_ERROR",
            ) from exc
        if workflow_run_id <= 0:
            raise TelegramOperatorError(
                error_code="INVALID_ARGUMENT",
                message="workflow_id must be a positive integer",
                http_status_code=HTTPStatus.BAD_REQUEST,
                failure_reason="UNKNOWN_ERROR",
            )
        return workflow_run_id

    @classmethod
    def _cost_result(cls, summary: CostScopeSnapshot) -> TelegramCostResult:
        return TelegramCostResult(
            status=summary.budget_status,
            reply_text=cls._cost_reply(summary),
            scope=summary.scope,
            workflow_run_id=summary.workflow_run_id,
            total_cost_usd=summary.total_cost_usd,
            llm_cost_usd=summary.llm_cost_usd,
            embedding_cost_usd=summary.embedding_cost_usd,
            unknown_cost_workflow_count=summary.unknown_cost_workflow_count,
            budget_status=summary.budget_status,
            budget_usd=summary.budget_usd,
            workflow_budget_exceeded_count=summary.workflow_budget_exceeded_count,
            workflow_budget_usd=summary.workflow_budget_usd,
        )

    @classmethod
    def _cost_reply(cls, summary: CostScopeSnapshot) -> str:
        scope_label = summary.scope
        if summary.scope == COST_SCOPE_WORKFLOW:
            scope_label = f"workflow #{summary.workflow_run_id}"
        lines = [
            f"Cost scope: {scope_label}",
            f"Workflows: {summary.workflow_count}",
            f"Recorded cost (known): {cls._format_cost(summary.total_cost_usd)}",
            f"LLM cost (proposal/QA): {cls._format_cost(summary.llm_cost_usd)}",
            f"Embedding cost (indexing): {cls._format_cost(summary.embedding_cost_usd)}",
            f"Unknown-cost workflows: {summary.unknown_cost_workflow_count}",
        ]
        if summary.unknown_cost_workflow_count:
            lines.append(
                "Pricing: unknown for "
                f"{summary.unknown_cost_workflow_count} workflow(s)"
            )
        if summary.budget_usd is None:
            lines.append(f"Budget status: {summary.budget_status}")
        else:
            lines.append(
                f"Budget: {cls._format_cost(summary.budget_usd)} "
                f"(status: {summary.budget_status})"
            )
        lines.append(
            f"Workflow-budget exceeded: {summary.workflow_budget_exceeded_count}"
        )
        return "\n".join(lines)

    @classmethod
    def _recent_workflow_reply(cls, workflows: list[WorkflowStatusView]) -> str:
        if not workflows:
            return "No workflow runs found."
        lines = ["Recent workflows:"]
        for workflow in workflows:
            lines.append(
                f"#{workflow.workflow_run_id} {cls._safe_label(workflow.workflow_type)} "
                f"— {cls._safe_label(workflow.status)} "
                f"— stale: {'yes' if workflow.stale else 'no'}"
            )
        return "\n".join(lines)

    @classmethod
    def _workflow_detail_reply(cls, workflow: WorkflowStatusView) -> str:
        lines = [
            f"Workflow #{workflow.workflow_run_id}",
            f"Type: {cls._safe_label(workflow.workflow_type)}",
            f"Status: {cls._safe_label(workflow.status)}",
            f"Age: {workflow.age_seconds:.3f}s",
            f"Stale: {'yes' if workflow.stale else 'no'}",
            f"Recorded cost: {cls._format_optional_cost(workflow.estimated_cost_usd)}",
        ]
        failure_reason = cls._safe_failure_reason(workflow.failure_reason)
        if failure_reason:
            lines.append(f"Failure reason: {failure_reason}")
        for key in cls._SAFE_METADATA_KEYS:
            value = workflow.metadata.get(key)
            if value is None:
                continue
            if isinstance(value, bool):
                display = "yes" if value else "no"
            elif isinstance(value, int) and not isinstance(value, bool):
                display = str(max(0, value))
            elif isinstance(value, str):
                display = cls._safe_label(value)
            else:
                continue
            lines.append(f"{key}: {display}")
        return "\n".join(lines)

    @staticmethod
    def _safe_label(value: object) -> str:
        normalized = sanitize_sensitive_text(str(value)).strip()
        return normalized[:80] or "unknown"

    @classmethod
    def _safe_failure_reason(cls, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        normalized = cls._safe_label(value).upper()
        return normalized if all(char.isalnum() or char == "_" for char in normalized) else "UNKNOWN_ERROR"

    @staticmethod
    def _format_cost(value: float) -> str:
        return f"${value:.6f}"

    @staticmethod
    def _format_optional_cost(value: Optional[float]) -> str:
        return TelegramOperatorOrchestrator._format_cost(value) if value is not None else "unknown"
