"""RQ jobs. The API enqueues them by name, so it does not import Docker or the pipeline."""

import hashlib
import json
import logging
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

from elevenlabs import ElevenLabs
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ReadTimeout
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings, meeting_dir
from ..db import get_sessionmaker
from ..enums import EventKind, Identity, MeetingStatus
from ..models import ActionItem, BotEvent, Chunk, Meeting, Summary, TranscriptSegment
from ..pipeline.chunks import summary_chunks, transcript_chunks
from ..pipeline.embed import embed_texts
from ..pipeline.stt import transcribe_file
from ..pipeline.summarize import summarize
from ..queues import PIPELINE_QUEUE, get_queue
from .launcher import start_bot, stop_container

logger = logging.getLogger(__name__)

# ffmpeg reports digital silence as -91 dB. Anything this quiet holds no speech.
SILENCE_DB = -60.0


def notice_audio(settings: Settings) -> Path | None:
    """The spoken notice, made once per text and voice. None leaves the bot with chat only."""
    if not settings.elevenlabs_api_key:
        return None
    # The service is part of the key: a mock's clip must not stand in for the real voice.
    key = "|".join(
        [
            settings.elevenlabs_base_url,
            settings.tts_model,
            settings.tts_voice_id,
            settings.notice_text,
        ]
    )
    path = settings.data_dir / "notice" / f"{hashlib.sha256(key.encode()).hexdigest()[:16]}.mp3"
    if path.exists():
        return path
    try:
        client = ElevenLabs(
            api_key=settings.elevenlabs_api_key, base_url=settings.elevenlabs_base_url or None
        )
        audio = client.text_to_speech.convert(
            voice_id=settings.tts_voice_id,
            text=settings.notice_text,
            model_id=settings.tts_model,
            output_format="mp3_44100_128",
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".partial")
        partial.write_bytes(b"".join(audio))
        partial.rename(path)
    except Exception:
        logger.exception("could not make the spoken notice; the bot will use chat only")
        return None
    return path


def run_bot(meeting_id: str) -> None:
    settings = get_settings()
    with get_sessionmaker()() as session:
        meeting = session.get(Meeting, uuid.UUID(meeting_id))
        if meeting is None or meeting.status != MeetingStatus.SCHEDULED:
            # Cancelled from the UI while it waited in the queue.
            return
        profile_dir = None
        if meeting.identity == Identity.SIGNED_IN:
            profile_dir = settings.data_dir / "accounts" / settings.bot_account_label / "profile"
            if not settings.bot_account_label or not (profile_dir / "Default").is_dir():
                meeting.status = MeetingStatus.FAILED
                meeting.failure_reason = (
                    "No signed-in bot account: run docker compose --profile login up bot-login"
                )
                session.commit()
                return
        container = start_bot(meeting, settings, notice_audio(settings), profile_dir)
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
        meeting.failure_reason = meeting.failure_reason or f"The bot exited with code {exit_code}"
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
    audio = meeting_dir(meeting_id) / "audio.ogg"
    try:
        volume = max_volume_db(audio)
        if volume < SILENCE_DB:
            # The bot can look healthy and still hear nothing, e.g. when the platform never
            # connected its audio. Say so instead of summarising silence.
            fail(meeting_id, f"The recording is silent (max {volume} dB)")
            return
        turns = transcribe_file(audio)
    except Exception as error:
        fail(meeting_id, f"Transcription failed: {error!r}"[:500])
        raise

    with get_sessionmaker()() as session:
        meeting = session.get(Meeting, uuid.UUID(meeting_id))
        session.execute(delete(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting.id))
        session.add_all(
            TranscriptSegment(
                namespace=meeting.namespace,
                meeting_id=meeting.id,
                idx=idx,
                speaker_label=turn.speaker_label,
                start_ms=turn.start_ms,
                end_ms=turn.end_ms,
                text=turn.text,
            )
            for idx, turn in enumerate(turns)
        )
        add_event(
            session, meeting, EventKind.TRANSCRIBED, segments=len(turns), max_volume_db=volume
        )
        if not turns:
            meeting.status = MeetingStatus.FAILED
            meeting.failure_reason = "No speech was found in the recording"
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
            participants_file = meeting_dir(meeting_id) / "participants.json"
            participants = (
                json.loads(participants_file.read_text()) if participants_file.is_file() else []
            )
            result = summarize(meeting.title, participants, segments)

            names = {speaker.label: speaker.name for speaker in result.speakers if speaker.name}
            for segment in segments:
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
        fail(meeting_id, f"Processing failed: {error!r}"[:500])
        raise
