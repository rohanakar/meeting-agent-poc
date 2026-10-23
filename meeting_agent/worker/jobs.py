"""RQ jobs. The API enqueues them by name, so it does not import Docker or the pipeline."""

import json
import logging
import re
import subprocess
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ReadTimeout
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import strings
from ..config import get_settings, meeting_dir
from ..db import get_sessionmaker
from ..enums import EventKind, MeetingStatus
from ..models import ActionItem, BotEvent, Chunk, Meeting, Summary, TranscriptSegment
from ..pipeline.chunks import summary_chunks, transcript_chunks
from ..pipeline.embed import embed_texts
from ..pipeline.speaker_map import map_speakers, name_turns, read_attendees
from ..pipeline.stt import transcribe_file
from ..pipeline.summarize import summarize
from ..queues import PIPELINE_QUEUE, get_queue
from .launcher import start_bot, stop_container

logger = logging.getLogger(__name__)

# ffmpeg reports digital silence as -91 dB. Anything this quiet holds no speech.
SILENCE_DB = -60.0
# The most speakers ElevenLabs can tell apart.
MAX_SPEAKERS = 32


# The spoken notice ships in the repo and the image, so every deploy has it; the bot plays it as
# it is. (It used to live in data/notice, which git ignores, so servers never got it.)
NOTICE_AUDIO = Path(__file__).parents[1] / "bot" / "disclaimer.mp3"


def notice_audio() -> Path | None:
    """The spoken notice. Without the file the bot gives the chat notice only."""
    if NOTICE_AUDIO.exists():
        return NOTICE_AUDIO
    logger.warning("%s is missing: the bot gives the chat notice only", NOTICE_AUDIO)
    return None


def run_bot(meeting_id: str) -> None:
    settings = get_settings()
    with get_sessionmaker()() as session:
        meeting = session.get(Meeting, uuid.UUID(meeting_id))
        if meeting is None or meeting.status != MeetingStatus.SCHEDULED:
            # Cancelled from the UI while it waited in the queue.
            return
        profile_dir = settings.data_dir / "accounts" / settings.bot_account_label / "profile"
        if not settings.bot_account_label or not (profile_dir / "Default").is_dir():
            meeting.status = MeetingStatus.FAILED
            meeting.failure_reason = strings.NO_BOT_ACCOUNT
            session.commit()
            return
        container = start_bot(meeting, settings, notice_audio(), profile_dir)
        meeting.container_id = container.id
        meeting.status = MeetingStatus.JOINING
        session.commit()

    # No session is held while the bot is in the meeting, which can last hours.
    try:
        exit_code = container.wait(timeout=settings.admission_timeout_s + settings.max_meeting_s)[
            "StatusCode"
        ]
    except (ReadTimeout, RequestsConnectionError):
        container.stop(timeout=30)
        exit_code = container.wait()["StatusCode"]
    (meeting_dir(meeting_id) / "bot.log").write_bytes(container.logs(tail=500))
    container.remove(force=True)

    with get_sessionmaker()() as session:
        meeting = session.get(Meeting, uuid.UUID(meeting_id))
        # A crash after the recording started still leaves audio worth transcribing.
        if (meeting_dir(meeting_id) / "audio.ogg").is_file():
            meeting.status = MeetingStatus.TRANSCRIBING
            session.commit()
            get_queue(PIPELINE_QUEUE).enqueue(
                "meeting_agent.worker.jobs.transcribe", meeting_id, job_timeout=30 * 60
            )
            return
        meeting.status = MeetingStatus.FAILED
        meeting.failure_reason = meeting.failure_reason or strings.BOT_EXITED.format(code=exit_code)
        session.commit()


def stop_bot(meeting_id: str) -> None:
    with get_sessionmaker()() as session:
        meeting = session.get(Meeting, uuid.UUID(meeting_id))
        if meeting is not None and meeting.container_id:
            stop_container(meeting.container_id)


def add_event(session: Session, meeting: Meeting, kind: EventKind, **detail: Any) -> None:
    session.add(
        BotEvent(namespace=meeting.namespace, meeting_id=meeting.id, kind=kind, detail=detail)
    )


def fail(meeting_id: str, reason: str) -> None:
    with get_sessionmaker()() as session:
        meeting = session.get(Meeting, uuid.UUID(meeting_id))
        meeting.status = MeetingStatus.FAILED
        meeting.failure_reason = reason
        add_event(session, meeting, EventKind.FAILED, reason=reason)
        session.commit()


def read_json(path: Path) -> list[Any]:
    """A list the bot wrote next to the recording; empty when this run did not write one."""
    return json.loads(path.read_text()) if path.is_file() else []


def max_volume_db(path: Path) -> float:
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    match = re.search(r"max_volume: (-?[\d.]+) dB", result.stderr)
    return float(match.group(1)) if match else -91.0


def transcribe(meeting_id: str) -> None:
    folder = meeting_dir(meeting_id)
    audio = folder / "audio.ogg"
    # Who spoke when, from the captions; their count tells ElevenLabs how many voices to find.
    marks = read_json(folder / "speaker_timeline.json")
    speakers = len({mark["name"] for mark in marks})
    try:
        volume = max_volume_db(audio)
        if volume < SILENCE_DB:
            # The bot can look healthy and still hear nothing, e.g. when the platform never
            # connected its audio. Say so instead of summarising silence.
            fail(meeting_id, strings.SILENT_RECORDING.format(volume=volume))
            return
        turns = transcribe_file(audio, num_speakers=min(speakers, MAX_SPEAKERS) or None)
    except Exception as error:
        fail(meeting_id, strings.TRANSCRIPTION_FAILED.format(error=error)[:500])
        raise

    # The speaker map: ElevenLabs' labels to the names (and emails) the bot saw in the meeting.
    attendees = read_attendees(read_json(folder / "participants.json"))
    matches = map_speakers(turns, marks, attendees)
    names = name_turns(turns, marks, matches, attendees)
    (folder / "speaker_map.json").write_text(
        json.dumps({label: asdict(match) for label, match in matches.items()}, indent=2)
    )

    with get_sessionmaker()() as session:
        meeting = session.get(Meeting, uuid.UUID(meeting_id))
        meeting.attendees = attendees
        session.execute(delete(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting.id))
        session.add_all(
            TranscriptSegment(
                namespace=meeting.namespace,
                meeting_id=meeting.id,
                idx=idx,
                speaker_label=turn.speaker_label,
                speaker_name=match.name if match else None,
                speaker_email=match.email if match else None,
                start_ms=turn.start_ms,
                end_ms=turn.end_ms,
                text=turn.text,
            )
            for idx, (turn, match) in enumerate(zip(turns, names, strict=True))
        )
        add_event(
            session,
            meeting,
            EventKind.TRANSCRIBED,
            segments=len(turns),
            max_volume_db=volume,
            speaker_map={label: match.name for label, match in matches.items()},
        )
        if not turns:
            meeting.status = MeetingStatus.FAILED
            meeting.failure_reason = strings.NO_SPEECH
            session.commit()
            return
        meeting.status = MeetingStatus.PROCESSING
        session.commit()
    get_queue(PIPELINE_QUEUE).enqueue(
        "meeting_agent.worker.jobs.process", meeting_id, job_timeout=15 * 60
    )


def process(meeting_id: str) -> None:
    try:
        with get_sessionmaker()() as session:
            meeting = session.get(Meeting, uuid.UUID(meeting_id))
            segments = list(
                session.scalars(
                    select(TranscriptSegment)
                    .where(TranscriptSegment.meeting_id == meeting.id)
                    .order_by(TranscriptSegment.idx)
                )
            )
            people = [
                f"{attendee['name']} <{attendee['email']}>"
                if attendee.get("email")
                else attendee["name"]
                for attendee in meeting.attendees
            ]
            # What people typed in the meeting's chat, in the order the bot saw it.
            chat = read_json(meeting_dir(meeting_id) / "chat.json")
            result = summarize(meeting.title, people, segments, chat)

            # Claude names only the speakers the captions left anonymous.
            names = {speaker.label: speaker.name for speaker in result.speakers if speaker.name}
            for segment in segments:
                if segment.speaker_name is None:
                    segment.speaker_name = names.get(segment.speaker_label)
            drafts = transcript_chunks(segments) + summary_chunks(result, segments)
            embeddings = embed_texts([draft.text for draft in drafts])

            # Processing again replaces the earlier result instead of adding to it.
            for model in (Summary, ActionItem, Chunk):
                session.execute(delete(model).where(model.meeting_id == meeting.id))
            session.add(
                Summary(
                    namespace=meeting.namespace,
                    meeting_id=meeting.id,
                    summary=result.summary,
                    key_points=result.key_points,
                    decisions=result.decisions,
                    model=get_settings().summary_model,
                )
            )
            session.add_all(
                ActionItem(
                    namespace=meeting.namespace,
                    meeting_id=meeting.id,
                    text=item.text,
                    owner=item.owner,
                    due_date=item.due,
                    segment_ids=item.segment_ids,
                )
                for item in result.action_items
            )
            session.add_all(
                Chunk(
                    namespace=meeting.namespace,
                    meeting_id=meeting.id,
                    kind=draft.kind,
                    text=draft.text,
                    start_ms=draft.start_ms,
                    end_ms=draft.end_ms,
                    meta=draft.meta,
                    embedding=embedding,
                )
                for draft, embedding in zip(drafts, embeddings, strict=True)
            )
            meeting.status = MeetingStatus.DONE
            add_event(
                session,
                meeting,
                EventKind.PROCESSED,
                action_items=len(result.action_items),
                chunks=len(drafts),
                named_speakers=len(names),
            )
            session.commit()
    except Exception as error:
        fail(meeting_id, strings.PROCESSING_FAILED.format(error=error)[:500])
        raise
