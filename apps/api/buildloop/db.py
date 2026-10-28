"""SQLite persistence: one generic JSON record table keeps the abstraction PostgreSQL-ready."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Index, String, Text, create_engine, delete, select
from sqlalchemy.orm import DeclarativeBase, Session

T = TypeVar("T", bound=BaseModel)


class Base(DeclarativeBase):
    pass


class Record(Base):
    __tablename__ = "records"
    id = Column(String, primary_key=True)
    project_id = Column(String, nullable=False, index=True)
    kind = Column(String, nullable=False)
    json = Column(Text, nullable=False)
    ts = Column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index("ix_records_project_kind", "project_id", "kind"),)


class Store:
    def __init__(self, url: str):
        kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, **kwargs)
        Base.metadata.create_all(self.engine)

    def put(self, project_id: str, kind: str, id_: str, model: BaseModel) -> None:
        with Session(self.engine) as s:
            rec = s.get(Record, id_)
            payload = model.model_dump_json()
            if rec is None:
                s.add(Record(id=id_, project_id=project_id, kind=kind, json=payload, ts=datetime.now(UTC)))
            else:
                rec.project_id = project_id
                rec.json = payload
                rec.ts = datetime.now(UTC)
            s.commit()

    def get(self, id_: str, cls: type[T]) -> T | None:
        with Session(self.engine) as s:
            rec = s.get(Record, id_)
            return cls.model_validate_json(rec.json) if rec else None

    def list(self, project_id: str, kind: str, cls: type[T]) -> list[T]:
        with Session(self.engine) as s:
            rows = s.execute(
                select(Record).where(Record.project_id == project_id, Record.kind == kind).order_by(Record.ts)
            ).scalars()
            return [cls.model_validate(json.loads(r.json)) for r in rows]

    def list_all(self, kind: str, cls: type[T]) -> list[T]:
        with Session(self.engine) as s:
            rows = s.execute(select(Record).where(Record.kind == kind).order_by(Record.ts)).scalars()
            return [cls.model_validate(json.loads(r.json)) for r in rows]

    def delete(self, id_: str) -> None:
        with Session(self.engine) as s:
            s.execute(delete(Record).where(Record.id == id_))
            s.commit()

    def close(self) -> None:
        self.engine.dispose()
