"""What gets embedded for search: transcript windows, the summary, and each action item."""

from dataclasses import dataclass, field
from typing import Any

from ..enums import ChunkKind
from ..models import TranscriptSegment
from .summarize import MeetingSummary, format_timestamp

# About 350 tokens: long enough to hold an exchange, short enough to stay on one topic.
MAX_CHUNK_CHARS = 1500


@dataclass
class ChunkDraft:
    kind: ChunkKind
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


def transcript_chunks(segments: list[TranscriptSegment]) -> list[ChunkDraft]:
    """Groups whole turns into windows. A turn is never split, so a long one is its own chunk."""
    groups: list[list[tuple[TranscriptSegment, str]]] = []
    size = 0
    for segment in segments:
        speaker = segment.speaker_name or segment.speaker_label
        line = f"[{format_timestamp(segment.start_ms)}] {speaker}: {segment.text}"
        if not groups or size + len(line) > MAX_CHUNK_CHARS:
            groups.append([])
            size = 0
        groups[-1].append((segment, line))
        size += len(line) + 1
    return [
        ChunkDraft(
            kind=ChunkKind.TRANSCRIPT,
            text="\n".join(line for _, line in group),
            start_ms=group[0][0].start_ms,
            end_ms=group[-1][0].end_ms,
            meta={
                "segment_ids": [group[0][0].idx, group[-1][0].idx],
                "speakers": sorted({seg.speaker_name or seg.speaker_label for seg, _ in group}),
            },
        )
        for group in groups
    ]


def summary_chunks(summary: MeetingSummary, segments: list[TranscriptSegment]) -> list[ChunkDraft]:
    start_by_idx = {segment.idx: segment.start_ms for segment in segments}
    end_by_idx = {segment.idx: segment.end_ms for segment in segments}
    overview = "\n".join(
        [summary.summary, *summary.key_points, *(f"Decision: {d}" for d in summary.decisions)]
    )
    drafts = [ChunkDraft(kind=ChunkKind.SUMMARY, text=overview)]
    for item in summary.action_items:
        cited = [idx for idx in item.segment_ids if idx in start_by_idx]
        parts = [item.text, f"Owner: {item.owner}" if item.owner else "", item.due or ""]
        drafts.append(
            ChunkDraft(
                kind=ChunkKind.ACTION_ITEM,
                text=" — ".join(filter(None, parts)),
                start_ms=min((start_by_idx[idx] for idx in cited), default=None),
                end_ms=max((end_by_idx[idx] for idx in cited), default=None),
                meta={"owner": item.owner, "segment_ids": cited},
            )
        )
    return drafts
