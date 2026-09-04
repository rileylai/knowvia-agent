from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _migration_heads() -> Sequence[str]:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    return tuple(ScriptDirectory.from_config(config).get_heads())


class SqlAlchemyReadinessProbe:
    def __init__(
        self,
        *,
        engine: Engine,
        migration_heads: Optional[Sequence[str]] = None,
    ) -> None:
        self._engine = engine
        self._migration_heads = tuple(
            _migration_heads() if migration_heads is None else migration_heads
        )

    def check_database(self) -> bool:
        with self._engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True

    def check_migration(self) -> bool:
        with self._engine.connect() as connection:
            current_revisions = tuple(
                row[0]
                for row in connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).fetchall()
            )
        return set(current_revisions) == set(self._migration_heads)

    def check_vector_extension(self) -> bool:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT 1 FROM pg_extension "
                    "WHERE extname = 'vector' LIMIT 1"
                )
            ).first()
        return row is not None
