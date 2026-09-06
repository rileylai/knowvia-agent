from importlib import import_module

from src.repositories.change_request_repository import ChangeRequestRepository
from src.repositories.api_idempotency_repository import ApiIdempotencyRepository
from src.repositories.chunk_repository import (
    ChunkBlockMappingError,
    ChunkRepository,
    ChunkRepositoryError,
    ChunkVectorQueryError,
    KnowledgeChunkUpsert,
    NotionChunkUpsert,
    RetrievalChunkCandidate,
    SemanticChunkMatch,
)
from src.repositories.conversation_repository import (
    ConversationMessageSnapshot,
    ConversationRepository,
    ConversationSessionSnapshot,
)
from src.repositories.memory_repository import LongTermMemorySnapshot, MemoryRepository
from src.repositories.notion_block_repository import NotionBlockRepository, NotionBlockSnapshot
from src.repositories.notion_page_repository import (
    NotionPageRepository,
    StaleNotionPageSnapshotError,
)
from src.repositories.knowledge_stats_repository import (
    KnowledgeStatsRepository,
    KnowledgeStatsSnapshot,
)
from src.repositories.source_document_repository import SourceDocumentRepository
from src.repositories.synthetic_data_repository import (
    SyntheticDataCounts,
    SyntheticDataRepository,
)
from src.repositories.workflow_run_repository import WorkflowRunRepository


def __getattr__(name: str):
    if name != "TelegramUpdateLedgerRepository":
        raise AttributeError(name)
    module = import_module("src.repositories.telegram_update_ledger_repository")
    value = getattr(module, name)
    globals()[name] = value
    return value

__all__ = [
    "ChangeRequestRepository",
    "ApiIdempotencyRepository",
    "ConversationMessageSnapshot",
    "ConversationRepository",
    "ConversationSessionSnapshot",
    "LongTermMemorySnapshot",
    "MemoryRepository",
    "ChunkBlockMappingError",
    "ChunkRepository",
    "ChunkRepositoryError",
    "ChunkVectorQueryError",
    "KnowledgeChunkUpsert",
    "NotionBlockRepository",
    "NotionChunkUpsert",
    "RetrievalChunkCandidate",
    "SemanticChunkMatch",
    "NotionBlockSnapshot",
    "NotionPageRepository",
    "StaleNotionPageSnapshotError",
    "KnowledgeStatsRepository",
    "KnowledgeStatsSnapshot",
    "SourceDocumentRepository",
    "SyntheticDataCounts",
    "SyntheticDataRepository",
    "TelegramUpdateLedgerRepository",
    "WorkflowRunRepository",
]
