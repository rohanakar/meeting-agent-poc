import json
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from rq.exceptions import NoSuchJobError
from rq.job import Job
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings, meeting_dir
from ..db import get_session
from ..enums import TERMINAL_STATUSES, MeetingStatus
from ..models import ActionItem, BotEvent, Meeting, Summary, TranscriptSegment
from ..platforms import UnsupportedMeetingUrl, detect_platform
from ..queues import BOTS_QUEUE, PIPELINE_QUEUE, get_queue, get_redis
from .auth import require_api_key
from .schemas import NAMESPACE_PATTERN, MeetingCreate, MeetingDetail, MeetingList, MeetingOut

router = APIRouter(prefix="/api/meetings", dependencies=[Depends(require_api_key)])

SessionDep = Annotated[Session, Depends(get_session)]
NamespaceQuery = Annotated[str, Query(pattern=NAMESPACE_PATTERN)]


def get_meeting(session: Session, meeting_id: uuid.UUID, namespace: str) -> Meeting:
    meeting = session.get(Meeting, meeting_id)
    if meeting is None or meeting.namespace != namespace:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Meeting not found")
    return meeting


@router.post("", status_code=status.HTTP_201_CREATED)
def create_meeting(body: MeetingCreate, session: SessionDep) -> MeetingOut:
    try:
        platform = detect_platform(body.meeting_url)
    except UnsupportedMeetingUrl as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    settings = get_settings()
    meeting = Meeting(
        namespace=body.namespace,
        platform=platform,
        meeting_url=body.meeting_url.strip(),
        title=body.title,
        join_at=body.join_at,
        egress_mode=body.egress_mode or settings.default_egress_mode,
        identity=body.identity,
        meta=body.metadata,
    )
    session.add(meeting)
    session.flush()

    # The bot may wait in a lobby and then stay for the whole meeting before the job returns.
    job_timeout = settings.admission_timeout_s + settings.max_meeting_s + 15 * 60
    queue = get_queue(BOTS_QUEUE)
    job_args = ("meeting_agent.worker.jobs.run_bot", str(meeting.id))
    if body.join_at and body.join_at > datetime.now(UTC):
        job = queue.enqueue_at(body.join_at, *job_args, job_timeout=job_timeout)
    else:
        job = queue.enqueue(*job_args, job_timeout=job_timeout)
    meeting.rq_job_id = job.id
    session.commit()
    return MeetingOut.model_validate(meeting)


@router.get("")
def list_meetings(
    session: SessionDep,
    namespace: NamespaceQuery,
    status_: Annotated[MeetingStatus | None, Query(alias="status")] = None,
    platform: str | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    metadata: Annotated[str | None, Query(description='JSON object, e.g. {"matter":"X"}')] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MeetingList:
    conditions = [Meeting.namespace == namespace]
    if status_:
        conditions.append(Meeting.status == status_)
    if platform:
        conditions.append(Meeting.platform == platform)
    if from_:
        conditions.append(Meeting.created_at >= from_)
    if to:
        conditions.append(Meeting.created_at < to)
    if metadata:
        try:
            conditions.append(Meeting.meta.contains(json.loads(metadata)))
        except json.JSONDecodeError as error:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "metadata must be JSON"
            ) from error

    total = session.scalar(select(func.count()).select_from(Meeting).where(*conditions)) or 0
    meetings = session.scalars(
        select(Meeting)
        .where(*conditions)
        .order_by(Meeting.created_at.desc(), Meeting.id)
        .limit(limit)
        .offset(offset)
    )
    return MeetingList(
        items=[MeetingOut.model_validate(meeting) for meeting in meetings],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{meeting_id}")
def meeting_detail(
    meeting_id: uuid.UUID, namespace: NamespaceQuery, session: SessionDep
) -> MeetingDetail:
    meeting = get_meeting(session, meeting_id, namespace)
    return MeetingDetail.model_validate(
        {
            "meeting": MeetingOut.model_validate(meeting),
            "events": session.scalars(
                select(BotEvent)
                .where(BotEvent.meeting_id == meeting_id)
                .order_by(BotEvent.at, BotEvent.id)
            ).all(),
            "segments": session.scalars(
                select(TranscriptSegment)
                .where(TranscriptSegment.meeting_id == meeting_id)
                .order_by(TranscriptSegment.idx)
            ).all(),
            "summary": session.scalar(select(Summary).where(Summary.meeting_id == meeting_id)),
            "action_items": session.scalars(
                select(ActionItem)
                .where(ActionItem.meeting_id == meeting_id)
                .order_by(ActionItem.id)
            ).all(),
        },
        from_attributes=True,
    )


@router.get("/{meeting_id}/audio")
def meeting_audio(
    meeting_id: uuid.UUID, namespace: NamespaceQuery, session: SessionDep
) -> FileResponse:
    get_meeting(session, meeting_id, namespace)
    path = meeting_dir(meeting_id) / "audio.ogg"
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No recording for this meeting")
    return FileResponse(path, media_type="audio/ogg")


@router.post("/{meeting_id}/stop")
def stop_meeting(
    meeting_id: uuid.UUID, namespace: NamespaceQuery, session: SessionDep
) -> MeetingOut:
    meeting = get_meeting(session, meeting_id, namespace)
    if meeting.status in TERMINAL_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"The meeting is already {meeting.status}")
    if meeting.status == MeetingStatus.SCHEDULED:
        # run_bot also checks the status when it starts, in case the cancel loses a race.
        meeting.status = MeetingStatus.CANCELLED
        if meeting.rq_job_id:
            try:
                Job.fetch(meeting.rq_job_id, connection=get_redis()).cancel()
            except NoSuchJobError:
                pass
    elif meeting.container_id:
        # The pipeline worker has the Docker socket; the API does not, as it faces users.
        get_queue(PIPELINE_QUEUE).enqueue("meeting_agent.worker.jobs.stop_bot", str(meeting.id))
    session.commit()
    return MeetingOut.model_validate(meeting)
