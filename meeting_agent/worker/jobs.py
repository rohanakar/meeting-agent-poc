"""RQ jobs. The API enqueues them by name, so it does not import Docker or the pipeline."""

import hashlib
import logging
import uuid
from pathlib import Path

from elevenlabs import ElevenLabs
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ReadTimeout

from ..config import Settings, get_settings, meeting_dir
from ..db import get_sessionmaker
from ..enums import Identity, MeetingStatus
from ..models import Meeting
from ..queues import PIPELINE_QUEUE, get_queue
from .launcher import start_bot, stop_container

logger = logging.getLogger(__name__)


def notice_audio(settings: Settings) -> Path | None:
    """The spoken notice, made once per text and voice. None leaves the bot with chat only."""
    if not settings.elevenlabs_api_key:
        return None
    key = f"{settings.tts_model}|{settings.tts_voice_id}|{settings.notice_text}"
    path = settings.data_dir / "notice" / f"{hashlib.sha256(key.encode()).hexdigest()[:16]}.mp3"
    if path.exists():
        return path
    try:
        audio = ElevenLabs(api_key=settings.elevenlabs_api_key).text_to_speech.convert(
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
        account_state_file = None
        if meeting.identity == Identity.SIGNED_IN:
            account_state_file = (
                settings.data_dir / "accounts" / f"{settings.bot_account_label}.enc"
            )
            if not settings.bot_account_label or not account_state_file.is_file():
                meeting.status = MeetingStatus.FAILED
                meeting.failure_reason = "No signed-in bot account (run scripts/bot_login.py)"
                session.commit()
                return
        container = start_bot(meeting, settings, notice_audio(settings), account_state_file)
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
