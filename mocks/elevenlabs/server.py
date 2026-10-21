"""A local stand-in for the two ElevenLabs endpoints meeting-agent calls.

    POST /v1/speech-to-text            the transcript of the meeting the audio belongs to
    POST /v1/text-to-speech/{voice}    the text, spoken by espeak-ng, as MP3

Speech to text does no recognition. It finds the meeting folder whose audio.ogg has the same
bytes as the upload (the data volume is mounted read-only) and answers from a transcript placed
there by hand:

    transcript.json   an ElevenLabs speech-to-text response, returned as it is
    transcript.txt    one turn per line, "Name: text", optionally starting with [mm:ss]

With neither, it answers from MOCK_DEFAULT_TRANSCRIPT (default_transcript.txt next to this file).
Every request needs the header xi-api-key: MOCK_API_KEY (default "test").

    uvicorn mocks.elevenlabs.server:app --port 11437
"""

import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger("mock_elevenlabs")

API_KEY = os.environ.get("MOCK_API_KEY", "test")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DEFAULT_TRANSCRIPT = Path(
    os.environ.get("MOCK_DEFAULT_TRANSCRIPT", Path(__file__).parent / "default_transcript.txt")
)

# Speaking pace for made-up word times: 150 words a minute, a short pause between turns.
SECONDS_PER_WORD = 0.4
SECONDS_BETWEEN_TURNS = 0.3
TURN_LINE = re.compile(r"^(?:\[(?:(\d+):)?(\d+):(\d+)\]\s*)?(?:([^:]{1,60}):\s+)?(.+)$")


def require_key(xi_api_key: Annotated[str, Header()] = "") -> None:
    if xi_api_key != API_KEY:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"status": "invalid_api_key", "message": "mock: use the key the mock was given"},
        )


app = FastAPI(title="mock-elevenlabs")
api = APIRouter(dependencies=[Depends(require_key)])


def words_from_text(transcript: str) -> dict[str, Any]:
    """Turns "Name: text" lines into the word list Scribe returns, with made-up times."""
    speaker_ids: dict[str, str] = {}
    words: list[dict[str, Any]] = []
    clock = 0.0
    speaker = "Speaker"
    for line in transcript.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        hours, minutes, seconds, name, text = TURN_LINE.match(line).groups()  # type: ignore[union-attr]
        if seconds is not None:
            clock = max(clock, int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds))
        speaker = name.strip() if name else speaker
        speaker_id = speaker_ids.setdefault(speaker, f"speaker_{len(speaker_ids)}")
        for index, word in enumerate(text.split()):
            if index:
                words.append(
                    {"text": " ", "start": clock, "end": clock, "type": "spacing",
                     "speaker_id": speaker_id, "logprob": 0.0}
                )  # fmt: skip
            words.append(
                {"text": word, "start": round(clock, 2), "end": round(clock + SECONDS_PER_WORD, 2),
                 "type": "word", "speaker_id": speaker_id, "logprob": 0.0}
            )  # fmt: skip
            clock += SECONDS_PER_WORD
        clock += SECONDS_BETWEEN_TURNS
    return {
        "language_code": "en",
        "language_probability": 1.0,
        "text": " ".join(word["text"] for word in words if word["type"] == "word"),
        "words": words,
        "audio_duration_secs": round(clock, 2),
    }


def meeting_folder_for(audio: bytes) -> Path | None:
    digest = hashlib.sha256(audio).digest()
    for candidate in (DATA_DIR / "meetings").glob("*/audio.ogg"):
        # Size first: hashing every recording on each request would read them all.
        if candidate.stat().st_size == len(audio) and (
            hashlib.sha256(candidate.read_bytes()).digest() == digest
        ):
            return candidate.parent
    return None


@api.post("/v1/speech-to-text")
async def speech_to_text(file: UploadFile) -> JSONResponse:
    folder = meeting_folder_for(await file.read())
    if folder is not None and (folder / "transcript.json").is_file():
        source = folder / "transcript.json"
        body = json.loads(source.read_text())
    else:
        source = DEFAULT_TRANSCRIPT
        if folder is not None and (folder / "transcript.txt").is_file():
            source = folder / "transcript.txt"
        body = words_from_text(source.read_text())
    logger.info("speech-to-text: meeting folder %s, transcript %s", folder, source)
    return JSONResponse(body, headers={"x-mock-transcript": str(source)})


@api.post("/v1/text-to-speech/{voice_id}")
async def text_to_speech(voice_id: str, request: Request) -> Response:
    text = (await request.json())["text"]
    with tempfile.TemporaryDirectory() as folder:
        speech = Path(folder) / "speech.wav"
        subprocess.run(
            ["espeak-ng", "-v", "en-us", "-s", "160", "-w", str(speech), text], check=True
        )
        mp3 = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", str(speech), "-f", "mp3", "-"],
            check=True,
            capture_output=True,
        ).stdout
    return Response(mp3, media_type="audio/mpeg")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(api)
