import re

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from ...enums import Admission
from .base import MeetingAdapter, any_text, click_if_visible, is_visible, poll_admission


def either(*selectors: str) -> str:
    return ", ".join(selectors)


class GoogleMeet(MeetingAdapter):
    # Meet's labels as of October 2026, English UI (the browser is forced to en-US). Replace any
    # of them in <selectors folder>/google_meet.json when Meet changes, without a rebuild.
    DEFAULT_SELECTORS = {
        "name_input": either('input[aria-label="Your name"]', 'input[placeholder="Your name"]'),
        # Without a camera Meet names it "Ask to join without camera", so only the start is
        # matched. Never "Switch here": it moves the account's session elsewhere into the bot.
        "join_button": re.compile(r"^(Ask to join|Join now|Join anyway)\b"),
        "no_devices_button": re.compile(r"Continue without microphone and camera"),
        "mic_off_button": either(
            '[role="button"][aria-label*="Turn off microphone"]',
            'button[aria-label*="Turn off microphone"]',
        ),
        "mic_on_button": either(
            '[role="button"][aria-label*="Turn on microphone"]',
            'button[aria-label*="Turn on microphone"]',
        ),
        "camera_off_button": either(
            '[role="button"][aria-label*="Turn off camera"]',
            'button[aria-label*="Turn off camera"]',
        ),
        "leave_button": 'button[aria-label="Leave call"]',
        "chat_button": 'button[aria-label^="Chat with everyone"]',
        "chat_input": either(
            'textarea[aria-label="Send a message"]', 'textarea[placeholder="Send a message"]'
        ),
        "participant_tile": "[data-participant-id]",
        "participant_name": "span.notranslate",
        "denied_text": any_text("denied your request to join"),
        "blocked_text": any_text("You can't join this call", "You can't join this video call"),
        "timeout_text": any_text("No one responded to your request"),
        "ended_text": any_text(
            "You've been removed from the meeting",
            "You left the meeting",
            "ended the meeting for everyone",
            "The call has ended",
            "This call has ended",
            "The meeting has ended",
            "Return to home screen",
        ),
    }

    async def join(self, page: Page, display_name: str) -> None:
        await page.wait_for_load_state("domcontentloaded")
        name_input = page.locator(self.s["name_input"])
        join_button = page.get_by_role("button", name=self.s["join_button"])
        try:
            await join_button.first.wait_for(timeout=60_000)
        except PlaywrightError:
            # The button's label changed, but the name field is still there: Enter in the
            # field asks to join, like the button does.
            if not await is_visible(name_input):
                raise
            await name_input.first.fill(display_name)
            await name_input.first.press("Enter")
            return
        await click_if_visible(page.get_by_role("button", name=self.s["no_devices_button"]))
        # A signed-in bot has no name field: Meet shows the account's name instead.
        if await is_visible(name_input):
            await name_input.first.fill(display_name)
        await click_if_visible(page.locator(self.s["mic_off_button"]))
        await click_if_visible(page.locator(self.s["camera_off_button"]))
        await join_button.first.click()

    async def wait_for_admission(self, page: Page, timeout_s: int) -> Admission:
        async def check() -> Admission | None:
            if await is_visible(page.locator(self.s["leave_button"])):
                return Admission.ADMITTED
            if await is_visible(page.get_by_text(self.s["denied_text"])):
                return Admission.DENIED
            if await is_visible(page.get_by_text(self.s["blocked_text"])):
                return Admission.BLOCKED
            if await is_visible(page.get_by_text(self.s["timeout_text"])):
                return Admission.TIMEOUT
            return None

        return await poll_admission(check, timeout_s)

    async def send_chat(self, page: Page, text: str) -> None:
        chat_input = page.locator(self.s["chat_input"]).first
        if not await is_visible(chat_input):
            if not await click_if_visible(page.locator(self.s["chat_button"])):
                # Meet's shortcut for the chat panel, for when the button's label changed.
                await page.keyboard.press("Control+Alt+c")
        await chat_input.wait_for(timeout=10_000)
        await chat_input.fill(text)
        await chat_input.press("Enter")

    async def set_microphone(self, page: Page, on: bool) -> None:
        await click_if_visible(page.locator(self.s["mic_on_button" if on else "mic_off_button"]))

    async def participant_names(self, page: Page) -> list[str]:
        # One entry per participant id: a tile and its inner parts can carry the same id.
        names: dict[str, str] = {}
        for tile in await page.locator(self.s["participant_tile"]).all():
            participant_id = await tile.get_attribute("data-participant-id")
            if not participant_id or participant_id in names:
                continue
            label = tile.locator(self.s["participant_name"]).first
            name = (await label.inner_text()).strip() if await label.count() else ""
            names[participant_id] = name or f"Participant {len(names) + 1}"
        return sorted(names.values())

    async def ended_reason(self, page: Page) -> str | None:
        if page.is_closed():
            return "page_closed"
        if await is_visible(page.get_by_text(self.s["ended_text"])):
            return "ended"
        return None

    async def leave(self, page: Page) -> None:
        await click_if_visible(page.locator(self.s["leave_button"]))
