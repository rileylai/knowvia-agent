from src.app.schemas.notion_index import (
    NotionFullIndexResponse,
    NotionIncrementalIndexRequest,
    NotionIncrementalIndexedPage,
    NotionIncrementalIndexResponse,
    NotionPageIndexRequest,
    NotionPageIndexResponse,
    NotionIndexStatusResponse,
)
from src.app.schemas.ops import ReadinessCheck, ReadinessResponse
from src.app.schemas.qa import QACitation, QARequest, QAResponse
from src.app.schemas.source_ingest import (
    ChatTextIngestionRequest,
    KnowledgeSourceResponse,
    SourceDocumentCreateRequest,
    SourceDocumentCreateResponse,
    YouTubeIngestionRequest,
    URLIngestionRequest,
)

_LEGACY_SCHEMA_EXPORTS = {
    name: ("src.app.schemas.supplement", name)
    for name in (
        "SupplementAcceptRequest",
        "SupplementEditLaterRequest",
        "SupplementProposeRequest",
        "SupplementProposeResponse",
        "SupplementCitation",
        "SupplementPendingItem",
        "SupplementPendingListResponse",
        "SupplementProposalContent",
        "SupplementRejectRequest",
        "SupplementReviewResponse",
        "SupplementTargetPage",
    )
}
_LEGACY_SCHEMA_EXPORTS.update(
    {
        name: ("src.app.schemas.telegram", name)
        for name in (
            "TelegramChatPayload",
            "TelegramDocumentPayload",
            "TelegramMessagePayload",
            "TelegramPhotoPayload",
            "TelegramWebhookRequest",
            "TelegramWebhookResponse",
        )
    }
)


def __getattr__(name: str):
    from importlib import import_module

    target = _LEGACY_SCHEMA_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module = import_module(target[0])
    value = getattr(module, target[1])
    globals()[name] = value
    return value

__all__ = [
    "QACitation",
    "NotionIncrementalIndexRequest",
    "NotionFullIndexResponse",
    "NotionIncrementalIndexedPage",
    "NotionIncrementalIndexResponse",
    "NotionPageIndexRequest",
    "NotionPageIndexResponse",
    "NotionIndexStatusResponse",
    "ReadinessCheck",
    "ReadinessResponse",
    "QARequest",
    "QAResponse",
    "ChatTextIngestionRequest",
    "KnowledgeSourceResponse",
    "SourceDocumentCreateRequest",
    "SourceDocumentCreateResponse",
    "SupplementAcceptRequest",
    "SupplementEditLaterRequest",
    "SupplementProposeRequest",
    "SupplementProposeResponse",
    "SupplementCitation",
    "SupplementPendingItem",
    "SupplementPendingListResponse",
    "SupplementProposalContent",
    "SupplementRejectRequest",
    "SupplementReviewResponse",
    "SupplementTargetPage",
    "YouTubeIngestionRequest",
    "URLIngestionRequest",
    "TelegramChatPayload",
    "TelegramDocumentPayload",
    "TelegramMessagePayload",
    "TelegramPhotoPayload",
    "TelegramWebhookRequest",
    "TelegramWebhookResponse",
]
