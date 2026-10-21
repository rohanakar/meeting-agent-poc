import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import FrameLocator, Locator, Page

from ...enums import Admission

# A page or the frame a platform renders its meeting in; both find elements the same way.
Root = Page | FrameLocator


class MeetingAdapter(Protocol):
    """What the bot needs from one meeting platform. Egress and recording are not its concern.

    Each adapter keeps its selectors in one block at the top, roles and visible text first,
    because platforms change their markup more often than their labels.
    """

    async def join(self, page: Page, display_name: str) -> None:
        """Fill in the name if asked, turn the camera and microphone off, ask to join."""

    async def wait_for_admission(self, page: Page, timeout_s: int) -> Admission: ...

    async def send_chat(self, page: Page, text: str) -> None: ...

    async def set_microphone(self, page: Page, on: bool) -> None: ...

    async def participant_names(self, page: Page) -> list[str]:
        """Everyone in the call, the bot included. Empty when the page does not say."""
        ...

    async def ended_reason(self, page: Page) -> str | None:
        """Why the meeting is over for the bot, or None while it goes on."""
        ...

    async def leave(self, page: Page) -> None: ...


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


def any_text(*phrases: str) -> re.Pattern[str]:
    return re.compile("|".join(re.escape(phrase) for phrase in phrases), re.IGNORECASE)
