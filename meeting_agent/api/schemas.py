import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..enums import EgressMode, EventKind, Identity

NAMESPACE_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,127}$"


class MeetingCreate(BaseModel):
    namespace: str = Field(pattern=NAMESPACE_PATTERN)
    meeting_url: str
    title: str = Field(min_length=1, max_length=500)
    join_at: datetime | None = None
    egress_mode: EgressMode | None = None
    identity: Identity = Identity.ANONYMOUS
    metadata: dict[str, str] = Field(default_factory=dict)


class MeetingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    namespace: str
    platform: str
    meeting_url: str
    title: str
    status: str
    join_at: datetime | None
    started_at: datetime | None
    ended_at: datetime | None
    egress_mode: str
    identity: str
    exit_ip: str | None
    admission: str | None
    failure_reason: str | None
    metadata: dict[str, Any] = Field(validation_alias="meta")
    created_at: datetime
    updated_at: datetime


class MeetingList(BaseModel):
    items: list[MeetingOut]
    total: int
    limit: int
    offset: int


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    at: datetime
    kind: str
    detail: dict[str, Any]


class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    idx: int
    speaker_label: str
    speaker_name: str | None
    start_ms: int
    end_ms: int
    text: str


class SummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    summary: str
    key_points: list[str]
    decisions: list[str]
    model: str


class ActionItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    text: str
    owner: str | None
    due_date: str | None
    segment_ids: list[int]


class MeetingDetail(BaseModel):
    meeting: MeetingOut
    events: list[EventOut]
    segments: list[SegmentOut]
    summary: SummaryOut | None
    action_items: list[ActionItemOut]


class EventIn(BaseModel):
    kind: EventKind
    detail: dict[str, Any] = Field(default_factory=dict)


class SearchHit(BaseModel):
    chunk_id: int
    meeting_id: uuid.UUID
    meeting_title: str
    platform: str
    kind: str
    text: str
    start_ms: int | None
    end_ms: int | None
    score: float
    created_at: datetime


class SearchResults(BaseModel):
    items: list[SearchHit]
    limit: int
    offset: int
