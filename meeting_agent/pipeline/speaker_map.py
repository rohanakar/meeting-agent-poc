"""Names (and emails) for ElevenLabs' anonymous speakers, from what the bot saw in the meeting.

Each ElevenLabs turn collects votes from the caption marks inside it: a mark is a moment in which
someone's captions moved (bot/speakers.py). A speaker label gets the name with most of its
votes, and that name's email when the meeting showed one.

ElevenLabs can also give two people one label, a quieter voice joining a louder one, so a turn
whose own captions clearly show someone else is named after them instead (name_turns).
"""

import bisect
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Protocol

# Captions appear a little after the words are spoken, so a turn takes marks from slightly
# before it starts to well after it ends.
MARKS_FROM_MS = -500
MARKS_UNTIL_MS = 2500
# Below this share of a label's votes the name is a guess, and the label stays anonymous.
MIN_SHARE = 0.5

# For one turn's own name, each mark counts once: for the turn spoken this long before it.
CAPTION_LAG_MS = 1000
# A shorter turn ("Yeah", "Mm") has few captions of its own; its marks are mostly the captions
# of whoever spoke before it, still catching up.
OWN_NAME_MIN_MS = 1000
OWN_NAME_MIN_MARKS = 2
OWN_NAME_MIN_SHARE = 0.6


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
    email_by_name = emails_by_name(attendees)
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


def name_turns(
    turns: list[Timed],
    marks: list[dict[str, Any]],
    matches: dict[str, SpeakerMatch],
    attendees: list[dict[str, Any]],
) -> list[SpeakerMatch | None]:
    """A name for each turn: its own captions' name when they clearly show one, else its label's.

    Turns are in time order. A mark belongs to the turn spoken CAPTION_LAG_MS before it, or to
    the last turn before that moment when nobody spoke then.
    """
    starts = [turn.start_ms for turn in turns]
    own: defaultdict[int, Counter[str]] = defaultdict(Counter)
    for mark in marks:
        index = bisect.bisect_right(starts, mark["t_ms"] - CAPTION_LAG_MS) - 1
        if index >= 0:
            own[index][mark["name"]] += 1
    email_by_name = emails_by_name(attendees)
    result = []
    for index, turn in enumerate(turns):
        match = matches.get(turn.speaker_label)
        total = sum(own[index].values())
        if turn.end_ms - turn.start_ms >= OWN_NAME_MIN_MS and total >= OWN_NAME_MIN_MARKS:
            name, count = own[index].most_common(1)[0]
            if count / total >= OWN_NAME_MIN_SHARE and (match is None or name != match.name):
                match = SpeakerMatch(
                    name=name,
                    email=email_by_name.get(name.casefold()),
                    share=round(count / total, 2),
                    votes=total,
                )
        result.append(match)
    return result


def emails_by_name(attendees: list[dict[str, Any]]) -> dict[str, str | None]:
    """Each attendee's email by their name in lower case."""
    return {
        attendee["name"].casefold(): attendee.get("email")
        for attendee in attendees
        if attendee.get("name")
    }


def read_attendees(raw: list[Any]) -> list[dict[str, Any]]:
    """participants.json as {"name", "email"} entries; older runs wrote bare names."""
    return [entry if isinstance(entry, dict) else {"name": entry, "email": None} for entry in raw]
