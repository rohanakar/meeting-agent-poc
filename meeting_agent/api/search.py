import json
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..db import get_session
from ..enums import ChunkKind
from ..models import Chunk, Meeting
from ..pipeline.embed import embed_texts
from .auth import require_api_key
from .schemas import NAMESPACE_PATTERN, SearchHit, SearchResults

router = APIRouter(prefix="/api/search", dependencies=[Depends(require_api_key)])


@router.get("")
def search(
    session: Annotated[Session, Depends(get_session)],
    namespace: Annotated[str, Query(pattern=NAMESPACE_PATTERN)],
    q: Annotated[str, Query(min_length=1, max_length=2000)],
    kind: ChunkKind | None = None,
    platform: str | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    metadata: Annotated[str | None, Query(description='JSON object, e.g. {"matter":"X"}')] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SearchResults:
    conditions = [Chunk.namespace == namespace]
    if kind:
        conditions.append(Chunk.kind == kind)
    if platform:
        conditions.append(Meeting.platform == platform)
    if from_:
        conditions.append(Chunk.created_at >= from_)
    if to:
        conditions.append(Chunk.created_at < to)
    if metadata:
        try:
            conditions.append(Meeting.meta.contains(json.loads(metadata)))
        except json.JSONDecodeError as error:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "metadata must be JSON"
            ) from error

    [query_embedding] = embed_texts([q])
    distance = Chunk.embedding.cosine_distance(query_embedding).label("distance")
    # Without this, HNSW returns its first candidates and the filters can leave fewer than
    # `limit` of them, or none. Relaxed order can swap close neighbours, so sort again below.
    session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    rows = session.execute(
        select(Chunk, Meeting.title, Meeting.platform, distance)
        .join(Meeting, Meeting.id == Chunk.meeting_id)
        .where(*conditions)
        .order_by(distance)
        .limit(limit)
        .offset(offset)
    ).all()
    hits = [
        SearchHit(
            chunk_id=chunk.id,
            meeting_id=chunk.meeting_id,
            meeting_title=title,
            platform=meeting_platform,
            kind=chunk.kind,
            text=chunk.text,
            start_ms=chunk.start_ms,
            end_ms=chunk.end_ms,
            score=1 - chunk_distance,
            created_at=chunk.created_at,
        )
        for chunk, title, meeting_platform, chunk_distance in rows
    ]
    hits.sort(key=lambda hit: hit.score, reverse=True)
    return SearchResults(items=hits, limit=limit, offset=offset)
