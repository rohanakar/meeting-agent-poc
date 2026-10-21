import re

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page

from ...enums import Admission
from .base import (
    EMAIL,
    MeetingAdapter,
    any_text,
    click_if_visible,
    is_visible,
    looks_like_name,
    poll_admission,
)


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
        # Tiles that show a shared screen, not a person.
        "presentation_text": any_text("Presentation", "is presenting", "You are presenting"),
        "captions_button": re.compile(r"^Turn on captions"),
        "captions_region": '[role="region"][aria-label*="Captions" i]',
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

    async def tile_name(self, tile: Locator) -> str:
        """A tile's name: its name label, else its accessible label, else the first line of its
        text that reads like a name. The label is not in the same place on every tile (camera
        on or off, host, the bot's own tile)."""
        label = tile.locator(self.s["participant_name"]).first
        if await label.count():
            name = (await label.inner_text()).strip()
            if name:
                return name
        aria_label = (await tile.get_attribute("aria-label") or "").strip()
        if aria_label and looks_like_name(aria_label):
            return aria_label
        lines = (await tile.inner_text()).splitlines()
        return next((line.strip() for line in lines if looks_like_name(line.strip())), "")

    async def participant_names(self, page: Page) -> list[str]:
        # One entry per participant id: a tile and its inner parts can carry the same id.
        names: dict[str, str] = {}
        for tile in await page.locator(self.s["participant_tile"]).all():
            participant_id = await tile.get_attribute("data-participant-id")
            if not participant_id or participant_id in names:
                continue
            if await tile.get_by_text(self.s["presentation_text"]).count():
                continue
            name = await self.tile_name(tile)
            names[participant_id] = name or f"Participant {len(names) + 1}"
        return sorted(names.values())

    async def participant_emails(self, page: Page) -> dict[str, str]:
        # Meet seldom puts an email in a tile; when it does (a label, a tooltip), keep it.
        emails: dict[str, str] = {}
        for tile in await page.locator(self.s["participant_tile"]).all():
            name = await self.tile_name(tile)
            found = EMAIL.search(await tile.inner_html())
            if name and found:
                emails[name] = found.group(0)
        return emails

    async def enable_captions(self, page: Page) -> bool:
        if not await click_if_visible(page.get_by_role("button", name=self.s["captions_button"])):
            # Meet's shortcut for captions, for when the button's label changed.
            await page.keyboard.press("c")
        try:
            # Attached, not visible: the area is empty, so without size, until someone speaks.
            await page.locator(self.s["captions_region"]).first.wait_for(
                state="attached", timeout=5000
            )
        except PlaywrightError:
            return False
        return True

    async def captions_text(self, page: Page) -> str | None:
        region = page.locator(self.s["captions_region"]).first
        if not await is_visible(region):
            return None
        return await region.inner_text()

    async def ended_reason(self, page: Page) -> str | None:
        if page.is_closed():
            return "page_closed"
        if await is_visible(page.get_by_text(self.s["ended_text"])):
            return "ended"
        return None

    async def leave(self, page: Page) -> None:
        await click_if_visible(page.locator(self.s["leave_button"]))
