from playwright.async_api import Page

from ... import strings
from ...enums import Admission
from .base import MeetingAdapter, Root, click_if_visible, is_visible, poll_admission, read_chat


async def client_root(page: Page) -> Root:
    if await page.locator(strings.ZOOM_CLIENT_FRAME).count():
        return page.frame_locator(strings.ZOOM_CLIENT_FRAME)
    return page


class ZoomWeb(MeetingAdapter):
    DEFAULT_SELECTORS = strings.ZOOM_SELECTORS

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
        # The button is named for what it does next: on means "mute my microphone" is shown.
        switch = root.locator(self.s["unmute_button" if on else "mute_button"])
        changed = root.locator(self.s["mute_button" if on else "unmute_button"])
        if not await is_visible(changed):
            await click_if_visible(switch)
            await changed.first.wait_for(timeout=5000)

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
        return [strings.PLACEHOLDER_NAME.format(number=n) for n in range(1, int(text) + 1)]

    async def chat_messages(self, page: Page) -> list[dict[str, str]]:
        return await read_chat(await client_root(page), self.s)

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
