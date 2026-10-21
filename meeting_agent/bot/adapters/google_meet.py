import re

from playwright.async_api import Page

from ...enums import Admission
from .base import any_text, click_if_visible, is_visible, poll_admission

# Selectors. Meet's labels as of October 2026, English UI (the browser is forced to en-US).
NAME_INPUT = 'input[aria-label="Your name"], input[placeholder="Your name"]'
MIC_OFF_BUTTON = (
    '[role="button"][aria-label*="Turn off microphone"], button[aria-label*="Turn off microphone"]'
)
MIC_ON_BUTTON = (
    '[role="button"][aria-label*="Turn on microphone"], button[aria-label*="Turn on microphone"]'
)
CAMERA_OFF_BUTTON = (
    '[role="button"][aria-label*="Turn off camera"], button[aria-label*="Turn off camera"]'
)
# Without a camera Meet names it "Ask to join without camera", so only the start is matched.
JOIN_BUTTON = re.compile(r"^(Ask to join|Join now|Join anyway|Switch here)\b")
NO_DEVICES_BUTTON = re.compile(r"Continue without microphone and camera")
LEAVE_BUTTON = 'button[aria-label="Leave call"]'
CHAT_BUTTON = 'button[aria-label^="Chat with everyone"]'
CHAT_INPUT = 'textarea[aria-label="Send a message"], textarea[placeholder="Send a message"]'
PARTICIPANT_TILE = "[data-participant-id]"
PARTICIPANT_NAME = "span.notranslate"
DISMISS_BUTTON = re.compile(r"^(Got it|Dismiss|Close|OK)$")

DENIED_TEXT = any_text("denied your request to join", "You've been removed from the meeting")
BLOCKED_TEXT = any_text("You can't join this call", "You can't join this video call")
TIMEOUT_TEXT = any_text("No one responded to your request")
ENDED_TEXT = any_text(
    "You've been removed from the meeting",
    "You left the meeting",
    "The call has ended",
    "The meeting has ended",
    "Return to home screen",
)


class GoogleMeet:
    async def join(self, page: Page, display_name: str) -> None:
        await page.wait_for_load_state("domcontentloaded")
        join_button = page.get_by_role("button", name=JOIN_BUTTON)
        await join_button.first.wait_for(timeout=60_000)
        await click_if_visible(page.get_by_role("button", name=NO_DEVICES_BUTTON))
        # The "Sign in with your Google account" tip shown to guests.
        await click_if_visible(page.get_by_role("button", name=DISMISS_BUTTON))
        # A signed-in bot has no name field: Meet shows the account's name instead.
        if await is_visible(page.locator(NAME_INPUT)):
            await page.locator(NAME_INPUT).first.fill(display_name)
        await click_if_visible(page.locator(MIC_OFF_BUTTON))
        await click_if_visible(page.locator(CAMERA_OFF_BUTTON))
        await join_button.first.click()

    async def wait_for_admission(self, page: Page, timeout_s: int) -> Admission:
        async def check() -> Admission | None:
            if await is_visible(page.locator(LEAVE_BUTTON)):
                return Admission.ADMITTED
            if await is_visible(page.get_by_text(DENIED_TEXT)):
                return Admission.DENIED
            if await is_visible(page.get_by_text(BLOCKED_TEXT)):
                return Admission.BLOCKED
            if await is_visible(page.get_by_text(TIMEOUT_TEXT)):
                return Admission.TIMEOUT
            return None

        result = await poll_admission(check, timeout_s)
        if result == Admission.ADMITTED:
            # Welcome tips cover the toolbar; closing them keeps the chat button clickable.
            await click_if_visible(page.get_by_role("button", name=DISMISS_BUTTON))
        return result

    async def send_chat(self, page: Page, text: str) -> None:
        if not await is_visible(page.locator(CHAT_INPUT)):
            await page.locator(CHAT_BUTTON).first.click()
        chat_input = page.locator(CHAT_INPUT).first
        await chat_input.wait_for(timeout=10_000)
        await chat_input.fill(text)
        await chat_input.press("Enter")

    async def set_microphone(self, page: Page, on: bool) -> None:
        await click_if_visible(page.locator(MIC_ON_BUTTON if on else MIC_OFF_BUTTON))

    async def participant_names(self, page: Page) -> list[str]:
        names = []
        for tile in await page.locator(PARTICIPANT_TILE).all():
            name = tile.locator(PARTICIPANT_NAME).first
            if await name.count():
                names.append((await name.inner_text()).strip())
        return sorted(set(filter(None, names)))

    async def ended_reason(self, page: Page) -> str | None:
        if page.is_closed():
            return "page_closed"
        if await is_visible(page.get_by_text(ENDED_TEXT)):
            return "ended"
        return None

    async def leave(self, page: Page) -> None:
        await click_if_visible(page.locator(LEAVE_BUTTON))
