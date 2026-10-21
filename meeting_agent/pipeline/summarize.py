from pathlib import Path

from anthropic import Anthropic
from pydantic import BaseModel, Field

from ..config import get_settings
from ..models import TranscriptSegment

SYSTEM_PROMPT = (Path(__file__).parents[2] / "prompts" / "summary.md").read_text()


def format_timestamp(ms: int) -> str:
    minutes, seconds = divmod(ms // 1000, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


class SummaryActionItem(BaseModel):
    text: str = Field(description="What must be done, as one imperative sentence")
    owner: str | None = Field(description="Who agreed to do it, as named in the meeting")
    due: str | None = Field(description="When, in the words used in the meeting")
    segment_ids: list[int] = Field(description="Ids of the segments where it was agreed")


class SpeakerName(BaseModel):
    label: str = Field(description='The transcript label, e.g. "Speaker 2"')
    name: str | None = Field(description="The person's name, only if the transcript shows it")


class MeetingSummary(BaseModel):
    summary: str
    key_points: list[str]
    decisions: list[str]
    action_items: list[SummaryActionItem]
    speakers: list[SpeakerName]


class SummaryRefused(Exception):
    pass


def summarize(
    title: str, participants: list[str], segments: list[TranscriptSegment]
) -> MeetingSummary:
    settings = get_settings()
    transcript = "\n".join(
        f"[S{segment.idx} {format_timestamp(segment.start_ms)}] "
        f"{segment.speaker_name or segment.speaker_label}: {segment.text}"
        for segment in segments
    )
    content = (
        f"Meeting title: {title}\n"
        f"People seen in the meeting: {', '.join(participants) or 'not known'}\n\n"
        f"<transcript>\n{transcript}\n</transcript>"
    )
    client = Anthropic(
        api_key=settings.anthropic_api_key, base_url=settings.anthropic_base_url or None
    )
    response = client.messages.parse(
        model=settings.summary_model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
        output_format=MeetingSummary,
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise SummaryRefused(f"No summary (stop reason {response.stop_reason})")
    return response.parsed_output
