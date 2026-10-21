import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import FrameLocator, Locator, Page

from ... import strings
from ...enums import Admission

logger = logging.getLogger(__name__)

# A page or the frame a platform renders its meeting in; both find elements the same way.
Root = Page | FrameLocator
# CSS selectors are strings; texts and accessible names to match are patterns.
Selectors = dict[str, str | re.Pattern[str]]

# Runs on every chat message element. The sender is the text of the message group's header
# without its time, so it is empty for the bot's own messages, which carry no name.
READ_CHAT = """(messages, [text, group, sender, time]) => messages.map((message, index) => {
    const box = message.closest(group);
    const name = box?.querySelector(sender)?.innerText ?? "";
    const stamp = box?.querySelector(time)?.innerText ?? "";
    return {
        id: message.getAttribute("data-message-id") || message.id || String(index),
        sender: (stamp ? name.replace(stamp, "") : name).trim(),
        text: (message.querySelector(text) ?? message).innerText.trim(),
    };
})"""


class MeetingAdapter:
    """What the bot needs from one meeting platform. Egress, media and recording are not its
    concern: those are the same on every platform.

    DEFAULT_SELECTORS holds everything the adapter looks for on the page, roles and visible text
    first, because platforms change their markup more often than their labels. A JSON file in
    the selectors folder can replace any of them without a rebuild (see load_selectors).
    """

    DEFAULT_SELECTORS: Selectors = {}

    def __init__(self, selectors: Selectors | None = None):
        self.s = selectors if selectors is not None else dict(self.DEFAULT_SELECTORS)

    async def join(self, page: Page, display_name: str) -> None:
        """Fill in the name if asked, turn the camera and microphone off, ask to join."""
        raise NotImplementedError

    async def wait_for_admission(self, page: Page, timeout_s: int) -> Admission:
        raise NotImplementedError

    async def send_chat(self, page: Page, text: str) -> None:
        raise NotImplementedError

    async def set_microphone(self, page: Page, on: bool) -> None:
        raise NotImplementedError

    async def participant_names(self, page: Page) -> list[str]:
        """Everyone in the call, the bot included, one entry each. Empty when the page does not
        say, which is not the same as the bot being alone."""
        raise NotImplementedError

    async def ended_reason(self, page: Page) -> str | None:
        """Why the meeting is over for the bot, or None while it goes on."""
        raise NotImplementedError

    async def own_name(self, page: Page) -> str | None:
        """The name on the bot's own tile, when the page tells it apart. A signed-in bot shows
        its account's name, which the bot does not know in advance."""
        return None

    async def participant_emails(self, page: Page) -> dict[str, str]:
        """Email by participant name, for whoever the page shows one. Most pages show none."""
        return {}

    async def enable_captions(self, page: Page) -> bool:
        """Turns on the platform's live captions for the bot. False when there are none."""
        return False

    async def captions_text(self, page: Page) -> str | None:
        """The caption area's text: each speaker's name on a line, then what they said."""
        return None

    async def chat_messages(self, page: Page) -> list[dict[str, str]]:
        """What the chat panel shows, oldest first: {"id", "sender", "text"} per message. The
        panel is the one the chat notice opened. Empty when the platform's chat is not read."""
        return []

    async def leave(self, page: Page) -> None:
        raise NotImplementedError


def load_selectors(folder: Path, platform: str, defaults: Selectors) -> Selectors:
    """The defaults, with any entries from <folder>/<platform>.json put in their place.

    A pattern's replacement is a regular expression string; a CSS selector's is a CSS string.
    Unknown keys are reported and skipped, so a typo cannot hide a default.
    """
    selectors = dict(defaults)
    path = folder / f"{platform}.json"
    if not path.is_file():
        return selectors
    for key, value in json.loads(path.read_text()).items():
        default = defaults.get(key)
        if default is None:
            logger.warning("%s: no selector named %r", path, key)
            continue
        selectors[key] = (
            re.compile(value, default.flags) if isinstance(default, re.Pattern) else value
        )
        logger.info("selector %s from %s", key, path)
    return selectors


async def is_visible(locator: Locator) -> bool:
    try:
        return await locator.first.is_visible()
    except PlaywrightError:
        return False


async def click_if_visible(locator: Locator) -> bool:
    if await is_visible(locator):
        await locator.first.click()
        return True
    return False


async def read_chat(root: Root, selectors: Selectors) -> list[dict[str, str]]:
    """Every chat message on the page, read in one call (see READ_CHAT)."""
    keys = ("chat_message_text", "chat_group", "chat_sender", "chat_time")
    return await root.locator(selectors["chat_message"]).evaluate_all(
        READ_CHAT, [selectors[key] for key in keys]
    )


async def clear_overlays(page: Page) -> int:
    """Closes tips and pop-ups in the page and its frames. Returns how many it closed."""
    closed = 0
    for frame in page.frames:
        buttons = frame.get_by_role("button", name=strings.DISMISS_BUTTON)
        try:
            for button in await buttons.all():
                if await button.is_visible():
                    await button.click(timeout=2000)
                    closed += 1
        except PlaywrightError:
            continue
    return closed


async def with_overlay_retry[T](page: Page, action: Callable[[], Awaitable[T]]) -> T:
    """Runs a page action; if it fails, closes whatever may cover the page and tries once more.

    A pop-up nobody planned for makes clicks time out ("element intercepts pointer events").
    """
    try:
        return await action()
    except PlaywrightError as error:
        closed = await clear_overlays(page)
        logger.warning("retrying after %r (closed %d pop-ups)", error, closed)
        return await action()


async def poll_admission(
    check: Callable[[], Awaitable[Admission | None]], timeout_s: int
) -> Admission:
    """Calls check every second until it gives a result or the time is up."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = await check()
        if result is not None:
            return result
        await asyncio.sleep(1)
    return Admission.TIMEOUT


def looks_like_name(line: str) -> bool:
    words = line.split()
    return 1 <= len(words) <= 4 and line[-1] not in ".?!,;:" and all(w[:1].isupper() for w in words)


EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
