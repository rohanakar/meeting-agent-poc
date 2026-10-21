import re

from playwright.async_api import Page

from ...enums import Admission
from .base import Root, any_text, click_if_visible, is_visible, poll_admission

# Selectors. Zoom web client (app.zoom.us/wc) as of October 2026, English UI.
# The client draws the meeting either in the page or in an iframe with this id.
CLIENT_FRAME = "iframe#webclient"
COOKIE_ACCEPT = "#onetrust-accept-btn-handler"
NAME_INPUT = "#input-for-name"
AGREE_BUTTON = re.compile(r"^I Agree$")
PREVIEW_MUTE = re.compile(r"^Mute$")
PREVIEW_STOP_VIDEO = re.compile(r"^Stop Video$")
JOIN_BUTTON = re.compile(r"^Join$")
JOIN_AUDIO = re.compile(r"Join Audio by Computer")
LEAVE_BUTTON = 'button[aria-label="Leave"], button.footer__leave-btn'
CHAT_BUTTON = 'button[aria-label*="chat panel" i]'
CHAT_INPUT = (
    '.chat-rtf-box__editor-outer [contenteditable="true"], textarea.chat-box__chat-textarea'
)
UNMUTE_BUTTON = 'button[aria-label="unmute my microphone"]'
MUTE_BUTTON = 'button[aria-label="mute my microphone"]'
PARTICIPANT_COUNT = ".footer-button__number-counter"

WAITING_TEXT = any_text("the meeting host will let you in soon", "Waiting for the host")
DENIED_TEXT = any_text("removed you from the waiting room", "You have been removed")
BLOCKED_TEXT = any_text(
    "This meeting link is invalid", "Passcode wrong", "This meeting ID is not valid"
)
ENDED_TEXT = any_text(
    "This meeting has been ended by host",
    "The meeting has been ended",
    "You have been removed from this meeting",
)


async def client_root(page: Page) -> Root:
    if await page.locator(CLIENT_FRAME).count():
        return page.frame_locator(CLIENT_FRAME)
    return page


class ZoomWeb:
    async def join(self, page: Page, display_name: str) -> None:
        await page.wait_for_load_state("domcontentloaded")
        await click_if_visible(page.locator(COOKIE_ACCEPT))
        root = await client_root(page)
        name_input = root.locator(NAME_INPUT)
        await name_input.wait_for(timeout=60_000)
        await name_input.fill(display_name)
        await click_if_visible(root.get_by_role("button", name=PREVIEW_MUTE))
        await click_if_visible(root.get_by_role("button", name=PREVIEW_STOP_VIDEO))
        await root.get_by_role("button", name=JOIN_BUTTON).first.click()
        await click_if_visible(root.get_by_role("button", name=AGREE_BUTTON))

    async def wait_for_admission(self, page: Page, timeout_s: int) -> Admission:
        async def check() -> Admission | None:
            root = await client_root(page)
            await click_if_visible(root.get_by_role("button", name=AGREE_BUTTON))
            if await is_visible(root.locator(LEAVE_BUTTON)):
                return Admission.ADMITTED
            if await is_visible(root.get_by_text(DENIED_TEXT)):
                return Admission.DENIED
            if await is_visible(root.get_by_text(BLOCKED_TEXT)):
                return Admission.BLOCKED
            return None

        result = await poll_admission(check, timeout_s)
        if result == Admission.ADMITTED:
            root = await client_root(page)
            # Without computer audio the bot hears nothing, and Zoom does not always join it.
            await click_if_visible(root.get_by_role("button", name=JOIN_AUDIO))
        return result

    async def send_chat(self, page: Page, text: str) -> None:
        root = await client_root(page)
        if not await is_visible(root.locator(CHAT_INPUT)):
            await root.locator(CHAT_BUTTON).first.click()
        chat_input = root.locator(CHAT_INPUT).first
        await chat_input.wait_for(timeout=10_000)
        await chat_input.click()
        await page.keyboard.type(text)
        await page.keyboard.press("Enter")

    async def set_microphone(self, page: Page, on: bool) -> None:
        root = await client_root(page)
        await click_if_visible(root.locator(UNMUTE_BUTTON if on else MUTE_BUTTON))

    async def participant_names(self, page: Page) -> list[str]:
        # The web client shows names only in a side panel; the footer count is enough to tell
        # when the bot is alone, so the names stay unknown.
        root = await client_root(page)
        counter = root.locator(PARTICIPANT_COUNT).first
        if not await is_visible(counter):
            return []
        text = (await counter.inner_text()).strip()
        if not text.isdigit():
            return []
        return [f"Participant {number}" for number in range(1, int(text) + 1)]

    async def ended_reason(self, page: Page) -> str | None:
        if page.is_closed():
            return "page_closed"
        root = await client_root(page)
        if await is_visible(root.get_by_text(ENDED_TEXT)):
            return "ended"
        return None

    async def leave(self, page: Page) -> None:
        root = await client_root(page)
        if await click_if_visible(root.locator(LEAVE_BUTTON)):
            await click_if_visible(root.get_by_role("button", name=re.compile(r"^Leave Meeting$")))
