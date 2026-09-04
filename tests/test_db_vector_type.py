import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from typing import List, Optional

from src.db.types import Vector


class _Base(DeclarativeBase):
    pass


class _VectorRecord(_Base):
    __tablename__ = "vector_records"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    embedding: Mapped[Optional[List[float]]] = mapped_column(Vector(3), nullable=True)


def _build_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _Base.metadata.create_all(engine)
    local_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return local_session()


def test_vector_type_round_trips_list_values_in_sqlite() -> None:
    session = _build_session()
    session.add(_VectorRecord(id=1, embedding=[0.1, 0.2, 0.3]))
    session.commit()

    record = session.get(_VectorRecord, 1)

    assert record is not None
    assert record.embedding == [0.1, 0.2, 0.3]


def test_vector_type_rejects_wrong_dimensions() -> None:
    session = _build_session()
    session.add(_VectorRecord(id=1, embedding=[0.1, 0.2]))

    with pytest.raises(StatementError) as exc_info:
        session.commit()

    assert "vector length 2 does not match expected 3" in str(exc_info.value)
