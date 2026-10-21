"""Speech to text with ElevenLabs Scribe v2, which returns words with speaker ids and times.

The turn grouping is ported from the framework's speech_provider_elevenlabs.py.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from elevenlabs import ElevenLabs
from elevenlabs.types import SpeechToTextChunkResponseModel, SpeechToTextWordResponseModel

from .. import strings
from ..config import get_settings

logger = logging.getLogger(__name__)

# A three-hour meeting outlasts the SDK's 240 s default.
TIMEOUT_S = 1800


@dataclass
class Turn:
    speaker_label: str
    start_ms: int
    end_ms: int
    text: str


def transcribe_file(path: Path) -> list[Turn]:
    settings = get_settings()
    client = ElevenLabs(
        api_key=settings.elevenlabs_api_key,
        base_url=settings.elevenlabs_base_url or None,
        timeout=TIMEOUT_S,
    )
    response = client.speech_to_text.convert(
        model_id=settings.stt_model,
        # Named with its extension: ElevenLabs reads the audio format from the file name.
        file=(path.name, path.read_bytes(), "audio/ogg"),
        diarize=True,
        timestamps_granularity="word",
    )
    # Multichannel and deferred responses come back only when asked for, and this never asks.
    if not isinstance(response, SpeechToTextChunkResponseModel):
        raise ValueError(strings.UNEXPECTED_TRANSCRIPT)
    return group_turns(response.words)


def group_turns(words: list[SpeechToTextWordResponseModel]) -> list[Turn]:
    """Splits the words into speaker turns. Speakers are labelled in order of first appearance,
    so the first voice heard is always Speaker 1."""
    label_by_speaker_id: dict[str | None, str] = {}
    turns: list[list[SpeechToTextWordResponseModel]] = []
    for entry in words:
        match entry.type:
            case "word":
                label_by_speaker_id.setdefault(
                    entry.speaker_id, f"Speaker {len(label_by_speaker_id) + 1}"
                )
                if not turns or entry.speaker_id != turns[-1][0].speaker_id:
                    turns.append([entry])
                else:
                    turns[-1].append(entry)
            case "spacing" | "audio_event":
                # Kept in the turn's text; before anyone speaks there is no turn to hold them.
                if turns:
                    turns[-1].append(entry)
            case _:
                logger.debug("unhandled transcript entry: %r", entry)

    result = []
    previous_end = 0.0
    for turn in turns:
        # Both timings are optional on the wire. An untimed turn sits where the turn before it
        # ended, so it stays in order and keeps its words.
        starts = [entry.start for entry in turn if entry.start is not None]
        ends = [entry.end for entry in turn if entry.end is not None]
        start = min(starts) if starts else previous_end
        end = max(ends) if ends else start
        result.append(
            Turn(
                speaker_label=label_by_speaker_id[turn[0].speaker_id],
                start_ms=round(start * 1000),
                end_ms=round(end * 1000),
                text="".join(entry.text for entry in turn).strip(),
            )
        )
        previous_end = end
    return result
