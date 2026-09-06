from src.app.api.routes.conversations import router as conversations_router
from src.app.api.routes.notion_index import router as notion_index_router
from src.app.api.routes.ops import router as ops_router
from src.app.api.routes.qa import router as qa_router
from src.app.api.routes.source_ingest import router as source_ingest_router

_LEGACY_ROUTERS = {
    "supplement_router": ("src.app.api.routes.supplement", "router"),
    "telegram_router": ("src.app.api.routes.telegram", "router"),
}


def __getattr__(name: str):
    from importlib import import_module

    target = _LEGACY_ROUTERS.get(name)
    if target is None:
        raise AttributeError(name)
    module = import_module(target[0])
    value = getattr(module, target[1])
    globals()[name] = value
    return value

__all__ = [
    "notion_index_router",
    "conversations_router",
    "ops_router",
    "qa_router",
    "source_ingest_router",
    "supplement_router",
    "telegram_router",
]
