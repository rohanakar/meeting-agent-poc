"""Drives tests/fake_meeting/index.html, so the bot container can be tested without a platform."""

from playwright.async_api import Page

from ...enums import Admission
from .base import MeetingAdapter, is_visible, poll_admission, read_chat

CHAT_SELECTORS = {
    "chat_message": "#messages li",
    "chat_message_text": ".text",
    "chat_group": "li",
    "chat_sender": ".sender",
    "chat_time": ".time",
}


class FakeMeeting(MeetingAdapter):
    async def join(self, page: Page, display_name: str) -> None:
        await page.locator("#name").fill(display_name)
        await page.locator("#ask").click()

    async def wait_for_admission(self, page: Page, timeout_s: int) -> Admission:
        async def check() -> Admission | None:
            if await is_visible(page.locator("#leave")):
                return Admission.ADMITTED
            if await is_visible(page.locator("#denied")):
                return Admission.DENIED
            return None

        return await poll_admission(check, timeout_s)

    async def send_chat(self, page: Page, text: str) -> None:
        await page.locator("#chat-input").fill(text)
        await page.locator("#chat-input").press("Enter")

    async def set_microphone(self, page: Page, on: bool) -> None:
        if (await page.locator("#mic").get_attribute("data-on") == "true") != on:
            await page.locator("#mic").click()

    async def participant_names(self, page: Page) -> list[str]:
        return await page.locator("#participants li").all_inner_texts()

    async def ended_reason(self, page: Page) -> str | None:
        return "ended" if await is_visible(page.locator("#ended")) else None

    async def enable_captions(self, page: Page) -> bool:
        await page.locator("#captions-on").click()
        return await is_visible(page.locator("#captions"))

    async def captions_text(self, page: Page) -> str | None:
        captions = page.locator("#captions")
        return await captions.inner_text() if await is_visible(captions) else None

    async def chat_messages(self, page: Page) -> list[dict[str, str]]:
        return await read_chat(page, CHAT_SELECTORS)

    async def leave(self, page: Page) -> None:
        if await is_visible(page.locator("#leave")):
            await page.locator("#leave").click()
