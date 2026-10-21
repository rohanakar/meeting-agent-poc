"""Who is speaking when, from the platform's live captions, sampled while the bot records.

ElevenLabs separates voices but cannot name them; captions put a name on every line. The
pipeline gives each ElevenLabs speaker the name whose captions moved while that speaker talked
(pipeline/speaker_map.py).
"""

import asyncio
import logging
import time
from collections.abc import Iterable

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from .adapters.base import MeetingAdapter, looks_like_name

logger = logging.getLogger(__name__)

SAMPLE_S = 0.5
# Meet labels the account's own speech "You"; for the bot that is only its spoken notice.
SELF_LABEL = "you"


def parse_captions(text: str, known_names: Iterable[str]) -> list[tuple[str, str]]:
    """The caption area's text as (speaker, words) blocks.

    A line is a speaker's name when it is a participant's name or "You", or when it reads like
    a full name (two to four words in title case, no closing punctuation): the bot may not have
    read that person's tile. A single word counts only while no names are known, since one
    capitalised word is often speech. Other lines are what was said.
    """
    exact = set(known_names)
    # Case is only a fallback: two people can share a name that differs only in capitals.
    folded_names = {name.casefold(): name for name in exact}
    blocks: list[list[str]] = []
    for line in (raw.strip() for raw in text.splitlines()):
        if not line:
            continue
        folded = line.casefold()
        name_like = looks_like_name(line) and (len(line.split()) >= 2 or not exact)
        if line in exact:
            blocks.append([line, ""])
        elif folded in folded_names or folded == SELF_LABEL or name_like:
            blocks.append([folded_names.get(folded, line), ""])
        elif blocks:
            blocks[-1][1] = f"{blocks[-1][1]} {line}".strip()
    return [(name, words) for name, words in blocks if words]


def speaking_now(previous: list[tuple[str, str]], current: list[tuple[str, str]]) -> set[str]:
    """The speakers whose caption block is new or grew since the last sample."""
    seen = set(previous)
    return {name for name, words in current if (name, words) not in seen}


async def sample_speakers(
    adapter: MeetingAdapter,
    page: Page,
    recording_started: float,
    known_names: set[str],
    marks: list[dict[str, object]],
    done: asyncio.Event,
) -> None:
    """Adds {"t_ms", "name"} to marks for every sample in which someone's captions moved.

    t_ms counts from the start of the recording, the same clock as the transcript's times.
    """
    previous: list[tuple[str, str]] = []
    while not done.is_set():
        try:
            text = await adapter.captions_text(page)
        except PlaywrightError:
            text = None
        if text:
            current = parse_captions(text, known_names)
            t_ms = round((time.monotonic() - recording_started) * 1000)
            for name in speaking_now(previous, current):
                if name.casefold() != SELF_LABEL:
                    marks.append({"t_ms": t_ms, "name": name})
            previous = current
        try:
            await asyncio.wait_for(done.wait(), timeout=SAMPLE_S)
        except TimeoutError:
            pass
