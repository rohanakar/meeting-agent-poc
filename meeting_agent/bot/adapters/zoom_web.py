import re

from playwright.async_api import Page

from ...enums import Admission
from .base import MeetingAdapter, Root, any_text, click_if_visible, is_visible, poll_admission

# The client draws the meeting either in the page or in an iframe with this id.
CLIENT_FRAME = "iframe#webclient"


async def client_root(page: Page) -> Root:
    if await page.locator(CLIENT_FRAME).count():
        return page.frame_locator(CLIENT_FRAME)
    return page


class ZoomWeb(MeetingAdapter):
    # Zoom web client (app.zoom.us/wc) as of October 2026, English UI. Replace any of them in
    # <selectors folder>/zoom.json when Zoom changes, without a rebuild.
    DEFAULT_SELECTORS = {
        "cookie_accept": "#onetrust-accept-btn-handler",
        "name_input": "#input-for-name",
        "agree_button": re.compile(r"^I Agree$"),
        "preview_mute_button": re.compile(r"^Mute$"),
        "preview_stop_video_button": re.compile(r"^Stop Video$"),
        "join_button": re.compile(r"^Join$"),
        "join_audio_button": re.compile(r"Join Audio by Computer"),
        "leave_button": 'button[aria-label="Leave"], button.footer__leave-btn',
        "leave_confirm_button": re.compile(r"^Leave Meeting$"),
        "chat_button": 'button[aria-label*="chat panel" i]',
        "chat_input": (
            '.chat-rtf-box__editor-outer [contenteditable="true"], textarea.chat-box__chat-textarea'
        ),
        "unmute_button": 'button[aria-label="unmute my microphone"]',
        "mute_button": 'button[aria-label="mute my microphone"]',
        "participant_count": ".footer-button__number-counter",
        "denied_text": any_text("removed you from the waiting room", "You have been removed"),
        "blocked_text": any_text(
            "This meeting link is invalid", "Passcode wrong", "This meeting ID is not valid"
        ),
        "ended_text": any_text(
            "This meeting has been ended by host",
            "The meeting has been ended",
            "You have been removed from this meeting",
        ),
    }

    async def join(self, page: Page, display_name: str) -> None:
        await page.wait_for_load_state("domcontentloaded")
        await click_if_visible(page.locator(self.s["cookie_accept"]))
        root = await client_root(page)
        name_input = root.locator(self.s["name_input"])
        await name_input.wait_for(timeout=60_000)
        await name_input.fill(display_name)
        await click_if_visible(root.get_by_role("button", name=self.s["preview_mute_button"]))
        await click_if_visible(root.get_by_role("button", name=self.s["preview_stop_video_button"]))
        await root.get_by_role("button", name=self.s["join_button"]).first.click()
        await click_if_visible(root.get_by_role("button", name=self.s["agree_button"]))

    async def wait_for_admission(self, page: Page, timeout_s: int) -> Admission:
        async def check() -> Admission | None:
            root = await client_root(page)
            await click_if_visible(root.get_by_role("button", name=self.s["agree_button"]))
            if await is_visible(root.locator(self.s["leave_button"])):
                return Admission.ADMITTED
            if await is_visible(root.get_by_text(self.s["denied_text"])):
                return Admission.DENIED
            if await is_visible(root.get_by_text(self.s["blocked_text"])):
                return Admission.BLOCKED
            return None

        result = await poll_admission(check, timeout_s)
        if result == Admission.ADMITTED:
            root = await client_root(page)
            # Without computer audio the bot hears nothing, and Zoom does not always join it.
            await click_if_visible(root.get_by_role("button", name=self.s["join_audio_button"]))
        return result

    async def send_chat(self, page: Page, text: str) -> None:
        root = await client_root(page)
        if not await is_visible(root.locator(self.s["chat_input"])):
            await root.locator(self.s["chat_button"]).first.click()
        chat_input = root.locator(self.s["chat_input"]).first
        await chat_input.wait_for(timeout=10_000)
        await chat_input.click()
        await page.keyboard.type(text)
        await page.keyboard.press("Enter")

    async def set_microphone(self, page: Page, on: bool) -> None:
        root = await client_root(page)
        await click_if_visible(root.locator(self.s["unmute_button" if on else "mute_button"]))

    async def participant_names(self, page: Page) -> list[str]:
        # The web client shows names only in a side panel; the footer count is enough to tell
        # when the bot is alone, so the names stay unknown.
        root = await client_root(page)
        counter = root.locator(self.s["participant_count"]).first
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
        if await is_visible(root.get_by_text(self.s["ended_text"])):
            return "ended"
        return None

    async def leave(self, page: Page) -> None:
        root = await client_root(page)
        if await click_if_visible(root.locator(self.s["leave_button"])):
            await click_if_visible(root.get_by_role("button", name=self.s["leave_confirm_button"]))
