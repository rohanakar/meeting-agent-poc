from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page

from ... import strings
from ...enums import Admission
from .base import (
    EMAIL,
    MeetingAdapter,
    click_if_visible,
    is_visible,
    looks_like_name,
    poll_admission,
    read_chat,
)


class GoogleMeet(MeetingAdapter):
    DEFAULT_SELECTORS = strings.MEET_SELECTORS

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
        # Right after joining, with every video stream starting, the panel can take a while.
        await chat_input.wait_for(timeout=30_000)
        await chat_input.fill(text)
        await chat_input.press("Enter")

    async def set_microphone(self, page: Page, on: bool) -> None:
        # The button is named for what it does next: on means "Turn off microphone" is shown.
        switch = page.locator(self.s["mic_on_button" if on else "mic_off_button"])
        changed = page.locator(self.s["mic_off_button" if on else "mic_on_button"])
        if not await is_visible(changed):
            # Admitted by its media, the bot can get here before Meet draws the call's controls:
            # with no button to click, the notice was never heard.
            await page.locator(self.s["leave_button"]).first.wait_for(timeout=30_000)
            await click_if_visible(switch)
            await changed.first.wait_for(timeout=5000)

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
            names[participant_id] = name or strings.PLACEHOLDER_NAME.format(number=len(names) + 1)
        return sorted(names.values())

    async def own_name(self, page: Page) -> str | None:
        candidates = set()
        for tile in await page.locator(self.s["participant_tile"]).all():
            if await tile.get_by_text(self.s["presentation_text"]).count():
                continue
            controls = tile.locator("[aria-label]")
            labels = [
                await control.get_attribute("aria-label") or "" for control in await controls.all()
            ]
            if not any(self.s["others_mic_control"].search(label) for label in labels):
                if name := await self.tile_name(tile):
                    candidates.add(name)
        # Only an unambiguous answer: a tile can lack the control for other reasons too.
        return candidates.pop() if len(candidates) == 1 else None

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

    async def chat_messages(self, page: Page) -> list[dict[str, str]]:
        return await read_chat(page, self.s)

    async def ended_reason(self, page: Page) -> str | None:
        if page.is_closed():
            return "page_closed"
        if await is_visible(page.get_by_text(self.s["ended_text"])):
            return "ended"
        return None

    async def leave(self, page: Page) -> None:
        await click_if_visible(page.locator(self.s["leave_button"]))
