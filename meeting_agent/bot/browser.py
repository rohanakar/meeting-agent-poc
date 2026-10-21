import json
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet
from playwright.async_api import BrowserContext, Playwright

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


async def launch(playwright: Playwright, settings: BotSettings) -> BrowserContext:
    proxy, preferences = chromium_egress(settings.egress_mode, settings.proxy_url)
    # A fresh profile per run: some egress settings exist only as profile preferences.
    profile = Path(tempfile.mkdtemp(prefix="chromium-profile-"))
    (profile / "Default").mkdir()
    (profile / "Default" / "Preferences").write_text(json.dumps(preferences))
    context = await playwright.chromium.launch_persistent_context(
        profile,
        headless=False,
        args=CHROMIUM_ARGS,
        proxy=proxy,
        viewport={"width": 1280, "height": 720},
        locale="en-US",
        permissions=["microphone", "camera"],
    )
    if settings.account_state_file:
        # Written by scripts/bot_login.py: a signed-in session, encrypted at rest. Google keeps
        # the session in cookies, the only part a persistent context can take back in.
        encrypted = Path(settings.account_state_file).read_bytes()
        state = json.loads(Fernet(settings.fernet_key).decrypt(encrypted))
        await context.add_cookies(state["cookies"])
    return context
