import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Playwright

from ..config import BotSettings
from .egress import chromium_egress
from .media import MEDIA_INIT_SCRIPT

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
    # Some egress settings exist only as profile preferences, so they go into the profile. The
    # profile is marked as closed cleanly: a copy of a profile in use otherwise opens with a
    # "Restore pages?" bubble over the page.
    preferences_file = profile / "Default" / "Preferences"
    existing = json.loads(preferences_file.read_text()) if preferences_file.exists() else {}
    clean_exit = {"profile": {"exit_type": "Normal", "exited_cleanly": True}}
    preferences_file.write_text(json.dumps(merged(merged(existing, preferences), clean_exit)))
    context = await playwright.chromium.launch_persistent_context(
        profile,
        headless=False,
        args=CHROMIUM_ARGS,
        proxy=proxy,
        viewport={"width": 1280, "height": 720},
        locale="en-US",
        permissions=["microphone", "camera"],
    )
    await context.add_init_script(script=MEDIA_INIT_SCRIPT)
    return context


def merged(base: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    """base with change applied, nested dictionaries merged key by key."""
    result = dict(base)
    for key, value in change.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merged(result[key], value)
        else:
            result[key] = value
    return result
