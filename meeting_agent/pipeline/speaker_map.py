"""Names (and emails) for ElevenLabs' anonymous speakers, from what the bot saw in the meeting.

Each ElevenLabs turn collects votes from the caption marks inside it: a mark is a moment in which
someone's captions moved (bot/speakers.py). A speaker label gets the name with most of its
votes, and that name's email when the meeting showed one.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Protocol

# Captions appear a little after the words are spoken, so a turn takes marks from slightly
# before it starts to well after it ends.
MARKS_FROM_MS = -500
MARKS_UNTIL_MS = 2500
# Below this share of a label's votes the name is a guess, and the label stays anonymous.
MIN_SHARE = 0.5


class Timed(Protocol):
    speaker_label: str
    start_ms: int
    end_ms: int


@dataclass
class SpeakerMatch:
    name: str
    email: str | None
    share: float
    """The share of the label's votes the name got"""
    votes: int


def map_speakers(
    turns: list[Timed], marks: list[dict[str, Any]], attendees: list[dict[str, Any]]
) -> dict[str, SpeakerMatch]:
    votes: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for turn in turns:
        for mark in marks:
            if turn.start_ms + MARKS_FROM_MS <= mark["t_ms"] <= turn.end_ms + MARKS_UNTIL_MS:
                votes[turn.speaker_label][mark["name"]] += 1
    email_by_name = {
        attendee["name"].casefold(): attendee.get("email")
        for attendee in attendees
        if attendee.get("name")
    }
    matches = {}
    for label, counter in votes.items():
        name, count = counter.most_common(1)[0]
        total = sum(counter.values())
        if count / total >= MIN_SHARE:
            matches[label] = SpeakerMatch(
                name=name,
                email=email_by_name.get(name.casefold()),
                share=round(count / total, 2),
                votes=total,
            )
    return matches


def read_attendees(raw: list[Any]) -> list[dict[str, Any]]:
    """participants.json as {"name", "email"} entries; older runs wrote bare names."""
    return [entry if isinstance(entry, dict) else {"name": entry, "email": None} for entry in raw]
