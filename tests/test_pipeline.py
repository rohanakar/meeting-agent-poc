import json
import subprocess
from typing import Any

import httpx
import httpx2
import pytest
import respx
from anthropic import Anthropic
from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from meeting_agent.config import meeting_dir
from meeting_agent.models import (
    EMBEDDING_DIMENSIONS,
    ActionItem,
    Chunk,
    Meeting,
    Summary,
    TranscriptSegment,
)
from meeting_agent.pipeline import embed, summarize
from meeting_agent.queues import PIPELINE_QUEUE, get_queue
from meeting_agent.worker import jobs

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"


def word(text: str, speaker: str, start: float | None, end: float | None) -> dict[str, Any]:
    return {
        "text": text,
        "type": "word",
        "speaker_id": speaker,
        "start": start,
        "end": end,
        "logprob": 0,
    }


def space(speaker: str) -> dict[str, Any]:
    return {"text": " ", "type": "spacing", "speaker_id": speaker, "logprob": 0}


WORDS = [
    word("Hello", "speaker_0", 0.0, 0.4),
    space("speaker_0"),
    word("team", "speaker_0", 0.5, 0.9),
    space("speaker_0"),
    word("Hi", "speaker_1", 1.2, 1.4),
    word("Anna", "speaker_1", None, None),
    word("Sure", "speaker_0", 2.0, 2.3),
]


def add_meeting(db: Engine, status: str) -> Meeting:
    with Session(db, expire_on_commit=False) as session:
        meeting = Meeting(
            namespace="acme",
            platform="google_meet",
            meeting_url="https://meet.google.com/abc-defg-hij",
            title="Budget review",
            egress_mode="direct",
            status=status,
            meta={},
        )
        session.add(meeting)
        session.commit()
        return meeting


def write_audio(meeting: Meeting, source: str) -> None:
    folder = meeting_dir(meeting.id)
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", source, "-t", "2",
         "-c:a", "libopus", str(folder / "audio.ogg")],
        check=True,
    )  # fmt: skip


def segment(idx: int, label: str, start_ms: int, text: str) -> TranscriptSegment:
    return TranscriptSegment(
        idx=idx, speaker_label=label, speaker_name=None, start_ms=start_ms, end_ms=start_ms + 900,
        text=text,
    )  # fmt: skip


@respx.mock
def test_transcribe_stores_turns_and_queues_processing(db: Engine) -> None:
    meeting = add_meeting(db, "transcribing")
    write_audio(meeting, "sine=frequency=300")
    route = respx.post(STT_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "language_code": "en",
                "language_probability": 0.99,
                "text": "Hello team Hi Anna Sure",
                "words": WORDS,
            },
        )
    )

    jobs.transcribe(str(meeting.id))

    with Session(db) as session:
        stored = session.scalars(select(TranscriptSegment).order_by(TranscriptSegment.idx)).all()
        assert [(s.idx, s.speaker_label, s.text) for s in stored] == [
            (0, "Speaker 1", "Hello team"),
            (1, "Speaker 2", "HiAnna"),
            (2, "Speaker 1", "Sure"),
        ]
        assert session.get(Meeting, meeting.id).status == "processing"
    assert b'name="diarize"' in route.calls[0].request.content
    [job] = get_queue(PIPELINE_QUEUE).jobs
    assert job.func_name == "meeting_agent.worker.jobs.process"


def test_a_silent_recording_fails_without_transcription(db: Engine) -> None:
    meeting = add_meeting(db, "transcribing")
    write_audio(meeting, "anullsrc")

    jobs.transcribe(str(meeting.id))

    with Session(db) as session:
        stored = session.get(Meeting, meeting.id)
        assert stored.status == "failed"
        assert "silent" in stored.failure_reason
    assert get_queue(PIPELINE_QUEUE).jobs == []


def test_process_summarises_the_transcript_and_chat_names_speakers_and_embeds(
    db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    meeting = add_meeting(db, "processing")
    folder = meeting_dir(meeting.id)
    folder.mkdir(parents=True, exist_ok=True)
    chat = [{"t_ms": 6_000, "sender": "Anna Kumar", "text": "Draft: https://example.com/budget"}]
    (folder / "chat.json").write_text(json.dumps(chat))
    with Session(db) as session:
        for item in [
            segment(0, "Speaker 1", 0, "Thanks Anna. Can you send the budget by Friday?"),
            segment(1, "Speaker 2", 4_000, "Yes, I will send it."),
        ]:
            item.namespace, item.meeting_id = "acme", meeting.id
            session.add(item)
        session.commit()
    summary = {
        "summary": "The team reviewed the budget.",
        "key_points": ["The budget is due."],
        "decisions": [],
        "action_items": [
            {"text": "Send the budget", "owner": "Anna", "due": "Friday", "segment_ids": [0, 1]}
        ],
        "speakers": [{"label": "Speaker 1", "name": None}, {"label": "Speaker 2", "name": "Anna"}],
    }
    requests: list[dict[str, Any]] = []

    # anthropic and openai speak httpx2, which respx does not patch.
    def claude(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": json.dumps(summary)}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 100, "output_tokens": 50},
            },
        )

    def embeddings(request: httpx2.Request) -> httpx2.Response:
        inputs = json.loads(request.content)["input"]
        data = [
            {"object": "embedding", "index": i, "embedding": [0.1] * EMBEDDING_DIMENSIONS}
            for i in range(len(inputs))
        ]
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "data": data,
                "model": "text-embedding-3-small",
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    monkeypatch.setattr(
        summarize,
        "Anthropic",
        lambda **client: Anthropic(
            **client, http_client=httpx2.Client(transport=httpx2.MockTransport(claude))
        ),
    )
    monkeypatch.setattr(
        embed,
        "OpenAI",
        lambda **client: OpenAI(
            **client, http_client=httpx2.Client(transport=httpx2.MockTransport(embeddings))
        ),
    )

    jobs.process(str(meeting.id))

    assert requests[0]["model"] == "claude-sonnet-5"
    content = requests[0]["messages"][0]["content"]
    assert "[S1 00:04] Speaker 2: Yes, I will send it." in content
    assert "<chat>\n[C0 00:06] Anna Kumar: Draft: https://example.com/budget\n</chat>" in content
    with Session(db) as session:
        assert session.get(Meeting, meeting.id).status == "done"
        assert session.scalar(select(Summary.summary)) == "The team reviewed the budget."
        [action] = session.scalars(select(ActionItem)).all()
        assert (action.owner, action.due_date, action.segment_ids) == ("Anna", "Friday", [0, 1])
        names = session.scalars(select(TranscriptSegment.speaker_name).order_by("idx")).all()
        assert names == [None, "Anna"]
        kinds = session.scalars(select(Chunk.kind).order_by(Chunk.id)).all()
        assert kinds == ["transcript", "summary", "action_item"]
        assert "Anna: Yes, I will send it." in session.scalar(select(Chunk.text).limit(1))
