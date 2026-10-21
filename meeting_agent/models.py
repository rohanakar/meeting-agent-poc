import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .enums import EgressMode, MeetingStatus

EMBEDDING_DIMENSIONS = 1536


class Base(DeclarativeBase):
    pass


class Namespaced:
    """Every row belongs to one organisation, and every query filters on it."""

    namespace: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Meeting(Namespaced, Base):
    __tablename__ = "meetings"
    __table_args__ = (
        Index("ix_meetings_namespace_created_at", "namespace", "created_at"),
        Index("ix_meetings_metadata", "metadata", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform: Mapped[str] = mapped_column(String(32))
    meeting_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default=MeetingStatus.SCHEDULED)
    join_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    egress_mode: Mapped[str] = mapped_column(String(16), default=EgressMode.DIRECT)
    exit_ip: Mapped[str | None] = mapped_column(String(64))
    admission: Mapped[str | None] = mapped_column(String(16))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    container_id: Mapped[str | None] = mapped_column(String(128))
    rq_job_id: Mapped[str | None] = mapped_column(String(64))
    # "metadata" is reserved on declarative classes, so the attribute has another name.
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    # Who the bot saw in the meeting: {"name", "email"}, the email only when the page showed one.
    attendees: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )


class BotEvent(Namespaced, Base):
    __tablename__ = "bot_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    kind: Mapped[str] = mapped_column(String(32))
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class TranscriptSegment(Namespaced, Base):
    __tablename__ = "transcript_segments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    idx: Mapped[int] = mapped_column(Integer)
    speaker_label: Mapped[str] = mapped_column(String(64))
    speaker_name: Mapped[str | None] = mapped_column(String(256))
    speaker_email: Mapped[str | None] = mapped_column(String(320))
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


class Summary(Namespaced, Base):
    __tablename__ = "summaries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), unique=True
    )
    summary: Mapped[str] = mapped_column(Text)
    key_points: Mapped[list[str]] = mapped_column(JSONB, default=list)
    decisions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    model: Mapped[str] = mapped_column(String(64))


class ActionItem(Namespaced, Base):
    __tablename__ = "action_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(256))
    # Free text: the model repeats what was said ("by Friday"), which is not always a date.
    due_date: Mapped[str | None] = mapped_column(String(64))
    segment_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)


class Chunk(Namespaced, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_namespace_created_at", "namespace", "created_at"),
        Index(
            "ix_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    start_ms: Mapped[int | None] = mapped_column(Integer)
    end_ms: Mapped[int | None] = mapped_column(Integer)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
