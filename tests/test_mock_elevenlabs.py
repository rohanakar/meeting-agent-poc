import json
import shutil

import pytest
from fastapi.testclient import TestClient

from meeting_agent.config import meeting_dir
from mocks.elevenlabs.server import app, words_from_text

KEY = {"xi-api-key": "test"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, headers=KEY)


def upload(client: TestClient, audio: bytes) -> dict:
    response = client.post(
        "/v1/speech-to-text",
        files={"file": ("audio.ogg", audio, "audio/ogg")},
        data={"model_id": "scribe_v2", "diarize": "true"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def meeting_with_audio(name: str, audio: bytes):
    folder = meeting_dir(name)
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    (folder / "audio.ogg").write_bytes(audio)
    return folder


def test_words_from_text_labels_speakers_and_times_words() -> None:
    result = words_from_text("# skipped\n[00:10] Anna: Hello team\nBen: Hi\n\nAnna: Bye")

    words = [word for word in result["words"] if word["type"] == "word"]
    assert [(w["text"], w["speaker_id"], w["start"]) for w in words] == [
        ("Hello", "speaker_0", 10.0),
        ("team", "speaker_0", 10.4),
        ("Hi", "speaker_1", 11.1),
        ("Bye", "speaker_0", 11.8),
    ]
    assert result["text"] == "Hello team Hi Bye"
    assert [w["type"] for w in result["words"][:3]] == ["word", "spacing", "word"]


def test_the_meeting_folder_transcript_is_used(client: TestClient) -> None:
    folder = meeting_with_audio("mock-txt", b"OggS meeting one")
    (folder / "transcript.txt").write_text("Anna: Budget by Friday\nBen: Agreed")
    meeting_with_audio("mock-other", b"OggS another meeting")

    result = upload(client, b"OggS meeting one")

    assert result["text"] == "Budget by Friday Agreed"


def test_a_json_transcript_is_returned_as_it_is(client: TestClient) -> None:
    folder = meeting_with_audio("mock-json", b"OggS meeting json")
    raw = {"language_code": "de", "language_probability": 0.9, "text": "Hallo", "words": []}
    (folder / "transcript.json").write_text(json.dumps(raw))

    assert upload(client, b"OggS meeting json") == raw


def test_unknown_audio_gets_the_default_transcript(client: TestClient) -> None:
    result = upload(client, b"OggS audio from no meeting folder")

    assert result["text"].startswith("Thanks for joining, everyone.")
    assert len({word["speaker_id"] for word in result["words"]}) == 3


def test_the_key_must_be_test() -> None:
    response = TestClient(app).post(
        "/v1/speech-to-text",
        files={"file": ("audio.ogg", b"x", "audio/ogg")},
        headers={"xi-api-key": "real-key"},
    )

    assert response.status_code == 401
    assert TestClient(app).get("/health").status_code == 200


@pytest.mark.skipif(shutil.which("espeak-ng") is None, reason="espeak-ng is not installed")
def test_text_to_speech_speaks_the_text(client: TestClient) -> None:
    response = client.post("/v1/text-to-speech/voice", json={"text": "This meeting is recorded."})

    assert response.headers["content-type"] == "audio/mpeg"
    assert len(response.content) > 1000
