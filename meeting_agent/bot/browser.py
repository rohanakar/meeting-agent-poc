import json
from pathlib import Path

from cryptography.fernet import Fernet
from playwright.async_api import Browser, BrowserContext, Playwright

from ..config import BotSettings
from .egress import chromium_egress

CHROMIUM_ARGS = [
    # Grants the microphone and camera prompts, which no one is there to click.
    "--use-fake-ui-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
    # Drops navigator.webdriver, which meeting pages read to spot automation.
    "--disable-blink-features=AutomationControlled",
    # Selectors match English labels, so the page must not follow the host's language.
    "--lang=en-US",
    "--window-size=1280,720",
    "--disable-dev-shm-usage",
]


async def launch(playwright: Playwright, settings: BotSettings) -> tuple[Browser, BrowserContext]:
    proxy, egress_args = chromium_egress(settings.egress_mode, settings.proxy_url)
    browser = await playwright.chromium.launch(
        headless=False, args=CHROMIUM_ARGS + egress_args, proxy=proxy
    )
    storage_state = None
    if settings.account_state_file:
        # Written by scripts/bot_login.py: a signed-in session, encrypted at rest.
        encrypted = Path(settings.account_state_file).read_bytes()
        storage_state = json.loads(Fernet(settings.fernet_key).decrypt(encrypted))
    context = await browser.new_context(
        viewport={"width": 1280, "height": 720},
        locale="en-US",
        permissions=["microphone", "camera"],
        storage_state=storage_state,
    )
    return browser, context
