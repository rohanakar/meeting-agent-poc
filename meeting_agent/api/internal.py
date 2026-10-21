import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import strings
from ..db import get_session
from ..enums import Admission, EventKind, MeetingStatus
from ..models import BotEvent, Meeting
from .auth import require_internal_token
from .schemas import EventIn

router = APIRouter(prefix="/internal", dependencies=[Depends(require_internal_token)])


@router.post("/meetings/{meeting_id}/events", status_code=status.HTTP_204_NO_CONTENT)
def add_event(
    meeting_id: uuid.UUID, body: EventIn, session: Annotated[Session, Depends(get_session)]
) -> None:
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, strings.MEETING_NOT_FOUND)
    session.add(
        BotEvent(
            namespace=meeting.namespace, meeting_id=meeting_id, kind=body.kind, detail=body.detail
        )
    )

    # The bot reports what happened; the meeting row keeps the latest state for the list page.
    # The worker sets the final status when the container exits.
    now = datetime.now(UTC)
    match body.kind:
        case EventKind.EGRESS_CHECKED:
            meeting.exit_ip = body.detail.get("http_ip")
        case EventKind.JOINING:
            meeting.status = MeetingStatus.JOINING
        case EventKind.WAITING_ADMISSION:
            meeting.status = MeetingStatus.WAITING
        case EventKind.ADMISSION:
            meeting.admission = body.detail.get("result")
            if meeting.admission == Admission.ADMITTED:
                meeting.status = MeetingStatus.IN_MEETING
        case EventKind.RECORDING_STARTED:
            meeting.started_at = now
        case EventKind.MEETING_ENDED:
            meeting.ended_at = now
        case EventKind.FAILED:
            meeting.failure_reason = body.detail.get("reason")
    session.commit()
