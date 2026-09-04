from importlib import import_module

from src.orchestrators.chat_text_ingestion_orchestrator import (
    ChatTextIngestionError,
    ChatTextIngestionOrchestrator,
    ChatTextIngestionResult,
    DEFAULT_CHAT_TEXT_SOURCE_DISPLAY_NAME,
    MVP_CHAT_TEXT_MAX_CHARS,
)
from src.orchestrators.document_ingestion_orchestrator import (
    DocumentIngestionError,
    DocumentIngestionOrchestrator,
    DocumentIngestionResult,
)
from src.orchestrators.image_ocr_ingestion_orchestrator import (
    ImageOCRIngestionError,
    ImageOCRIngestionOrchestrator,
    ImageOCRIngestionResult,
    ImageUploadInput,
)
from src.orchestrators.notion_incremental_index_orchestrator import (
    NotionIncrementalIndexOrchestrator,
    NotionIncrementalIndexResult,
    NotionIncrementalIndexedPageResult,
)
from src.orchestrators.notion_full_index_orchestrator import (
    NotionFullIndexOrchestrator,
    NotionFullIndexResult,
    NotionFullIndexedPageResult,
)
from src.orchestrators.notion_page_index_orchestrator import (
    NotionPageIndexError,
    NotionPageIndexOrchestrator,
    NotionPageIndexResult,
    PreparedNotionPageSnapshot,
)
from src.orchestrators.qa_orchestrator import (
    QAOrchestrator,
    QAOrchestratorError,
    QAResult,
    QACitationResult,
)
from src.orchestrators.source_document_orchestrator import (
    SourceDocumentCreateResult,
    SourceDocumentOrchestrator,
    SourceDocumentWorkflowError,
)
from src.orchestrators.url_ingestion_orchestrator import (
    URLIngestionError,
    URLIngestionOrchestrator,
    URLIngestionResult,
)
from src.orchestrators.youtube_ingestion_orchestrator import (
    YouTubeIngestionError,
    YouTubeIngestionOrchestrator,
    YouTubeIngestionResult,
)


_LEGACY_EXPORTS = {
    name: (module_name, name)
    for module_name, names in {
        "src.orchestrators.supplement_propose_orchestrator": (
            "DEFAULT_SUPPLEMENT_MODEL",
            "DEFAULT_SUPPLEMENT_PROVIDER_NAME",
            "SupplementProposeError",
            "SupplementProposeOrchestrator",
            "SupplementProposeResult",
        ),
        "src.orchestrators.supplement_review_orchestrator": (
            "REVIEW_ACTION_ACCEPT",
            "REVIEW_ACTION_EDIT_LATER",
            "REVIEW_ACTION_REJECT",
            "SupplementReviewError",
            "SupplementReviewOrchestrator",
            "SupplementReviewResult",
        ),
        "src.orchestrators.supplement_proposal_schema": (
            "SupplementBodyRepairSchema",
            "SupplementProposalCitationSchema",
            "SupplementProposalGeneratedSchema",
            "SupplementProposalSchema",
            "SupplementProposalSourceSchema",
            "SupplementSummaryRepairSchema",
            "SupplementTitleRepairSchema",
            "SupplementProposalValidationError",
            "build_deterministic_supplement_source",
            "merge_generated_supplement_proposal",
            "parse_supplement_generated_json",
            "parse_supplement_proposal_json",
            "parse_supplement_body_repair_json",
            "parse_supplement_summary_repair_json",
            "parse_supplement_title_repair_json",
        ),
        "src.orchestrators.supplement_query_orchestrator": (
            "SupplementCitationResult",
            "SupplementProposalContentResult",
            "SupplementQueryError",
            "SupplementQueryOrchestrator",
            "SupplementReviewItemResult",
            "SupplementTargetResult",
        ),
        "src.orchestrators.telegram_gateway_orchestrator": (
            "TelegramCallbackAttachment",
            "TelegramGatewayError",
            "TelegramGatewayOrchestrator",
            "TelegramGatewayResult",
        ),
        "src.orchestrators.telegram_ingestion_orchestrator": (
            "TelegramDocumentAttachment",
            "TelegramIngestionCommandResult",
            "TelegramIngestionError",
            "TelegramIngestionOrchestrator",
            "TelegramPhotoAttachment",
        ),
        "src.orchestrators.telegram_index_orchestrator": (
            "TelegramFullIndexView",
            "TelegramIndexError",
            "TelegramIndexOrchestrator",
            "TelegramIndexResult",
        ),
        "src.orchestrators.telegram_operator_orchestrator": (
            "TelegramCostResult",
            "TelegramPendingItem",
            "TelegramPendingResult",
            "TelegramOperatorError",
            "TelegramOperatorOrchestrator",
            "TelegramStatsResult",
            "TelegramStatusCheck",
            "TelegramStatusResult",
            "TelegramWorkflowResult",
        ),
        "src.orchestrators.telegram_page_orchestrator": (
            "TelegramPageItem",
            "TelegramPageOrchestrator",
            "TelegramPagesResult",
        ),
        "src.orchestrators.telegram_qa_orchestrator": (
            "ASK_USAGE_REPLY",
            "TelegramQACommandResult",
            "TelegramQAError",
            "TelegramQAOrchestrator",
        ),
        "src.orchestrators.telegram_review_orchestrator": (
            "ACCEPT_USAGE_REPLY",
            "REJECT_USAGE_REPLY",
            "TelegramReviewCommandResult",
            "TelegramReviewError",
            "TelegramReviewOrchestrator",
        ),
        "src.orchestrators.telegram_sync_orchestrator": (
            "TelegramSyncError",
            "TelegramSyncOrchestrator",
            "TelegramSyncResult",
            "TelegramSyncView",
        ),
    }.items()
    for name in names
}


def __getattr__(name: str):
    target = _LEGACY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module = import_module(target[0])
    value = getattr(module, target[1])
    globals()[name] = value
    return value


__all__ = [
    "NotionIncrementalIndexOrchestrator",
    "NotionIncrementalIndexResult",
    "NotionIncrementalIndexedPageResult",
    "NotionFullIndexOrchestrator",
    "NotionFullIndexResult",
    "NotionFullIndexedPageResult",
    "ChatTextIngestionError",
    "ChatTextIngestionOrchestrator",
    "ChatTextIngestionResult",
    "DEFAULT_CHAT_TEXT_SOURCE_DISPLAY_NAME",
    "MVP_CHAT_TEXT_MAX_CHARS",
    "DocumentIngestionError",
    "DocumentIngestionOrchestrator",
    "DocumentIngestionResult",
    "ImageOCRIngestionError",
    "ImageOCRIngestionOrchestrator",
    "ImageOCRIngestionResult",
    "ImageUploadInput",
    "NotionPageIndexError",
    "NotionPageIndexOrchestrator",
    "NotionPageIndexResult",
    "PreparedNotionPageSnapshot",
    "QACitationResult",
    "QAOrchestrator",
    "QAOrchestratorError",
    "QAResult",
    "SourceDocumentCreateResult",
    "SourceDocumentOrchestrator",
    "SourceDocumentWorkflowError",
    "DEFAULT_SUPPLEMENT_MODEL",
    "DEFAULT_SUPPLEMENT_PROVIDER_NAME",
    "REVIEW_ACTION_ACCEPT",
    "REVIEW_ACTION_EDIT_LATER",
    "REVIEW_ACTION_REJECT",
    "SupplementProposeError",
    "SupplementProposeOrchestrator",
    "SupplementProposeResult",
    "SupplementReviewError",
    "SupplementReviewOrchestrator",
    "SupplementReviewResult",
    "SupplementProposalSchema",
    "SupplementProposalCitationSchema",
    "SupplementProposalGeneratedSchema",
    "SupplementProposalSourceSchema",
    "SupplementBodyRepairSchema",
    "SupplementSummaryRepairSchema",
    "SupplementTitleRepairSchema",
    "SupplementProposalValidationError",
    "build_deterministic_supplement_source",
    "merge_generated_supplement_proposal",
    "parse_supplement_generated_json",
    "parse_supplement_proposal_json",
    "parse_supplement_body_repair_json",
    "parse_supplement_summary_repair_json",
    "parse_supplement_title_repair_json",
    "SupplementCitationResult",
    "SupplementProposalContentResult",
    "SupplementQueryError",
    "SupplementQueryOrchestrator",
    "SupplementReviewItemResult",
    "SupplementTargetResult",
    "TelegramGatewayError",
    "TelegramCallbackAttachment",
    "TelegramGatewayOrchestrator",
    "TelegramGatewayResult",
    "TelegramDocumentAttachment",
    "TelegramIngestionCommandResult",
    "TelegramIngestionError",
    "TelegramIngestionOrchestrator",
    "TelegramPhotoAttachment",
    "TelegramFullIndexView",
    "TelegramIndexError",
    "TelegramIndexOrchestrator",
    "TelegramIndexResult",
    "TelegramCostResult",
    "TelegramPendingItem",
    "TelegramPendingResult",
    "TelegramOperatorError",
    "TelegramOperatorOrchestrator",
    "TelegramStatsResult",
    "TelegramStatusCheck",
    "TelegramStatusResult",
    "TelegramWorkflowResult",
    "TelegramPageItem",
    "TelegramPageOrchestrator",
    "TelegramPagesResult",
    "ASK_USAGE_REPLY",
    "TelegramQACommandResult",
    "TelegramQAError",
    "TelegramQAOrchestrator",
    "ACCEPT_USAGE_REPLY",
    "REJECT_USAGE_REPLY",
    "TelegramReviewCommandResult",
    "TelegramReviewError",
    "TelegramReviewOrchestrator",
    "TelegramSyncError",
    "TelegramSyncOrchestrator",
    "TelegramSyncResult",
    "TelegramSyncView",
    "URLIngestionError",
    "URLIngestionOrchestrator",
    "URLIngestionResult",
    "YouTubeIngestionError",
    "YouTubeIngestionOrchestrator",
    "YouTubeIngestionResult",
]
