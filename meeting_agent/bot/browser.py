import json
import shutil
import tempfile
from pathlib import Path

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
    profile = Path(tempfile.mkdtemp(prefix="chromium-profile-")) / "profile"
    if settings.profile_dir:
        # A copy of the signed-in profile: bots can run at the same time, and Chromium locks the
        # profile it uses. The copy keeps the original's Google session.
        shutil.copytree(
            settings.profile_dir, profile, ignore=shutil.ignore_patterns("Singleton*", "*.lock")
        )
    (profile / "Default").mkdir(parents=True, exist_ok=True)
    # Some egress settings exist only as profile preferences, so they go into the profile.
    preferences_file = profile / "Default" / "Preferences"
    existing = json.loads(preferences_file.read_text()) if preferences_file.exists() else {}
    preferences_file.write_text(json.dumps(existing | preferences))
    context = await playwright.chromium.launch_persistent_context(
        profile,
        headless=False,
        args=CHROMIUM_ARGS,
        proxy=proxy,
        viewport={"width": 1280, "height": 720},
        locale="en-US",
        permissions=["microphone", "camera"],
    )
    return context
