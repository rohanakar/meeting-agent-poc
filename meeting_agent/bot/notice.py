import asyncio
import contextlib
import logging

from playwright.async_api import Page

from ..config import BotSettings
from ..enums import EventKind
from .adapters.base import MeetingAdapter, with_overlay_retry
from .report import Reporter

logger = logging.getLogger(__name__)

# Set up by docker/bot-entrypoint.sh: whatever plays here reaches Chromium's microphone.
NOTICE_SINK = "notice_in"


async def give_notice(
    adapter: MeetingAdapter, page: Page, settings: BotSettings, reporter: Reporter
) -> bool:
    """Tells the meeting it is being recorded, in chat and out loud.

    Returns True if at least one notice went out; the bot records only then.
    """
    posted = spoken = False
    try:
        await with_overlay_retry(page, lambda: adapter.send_chat(page, settings.notice_text))
        posted = True
    except Exception as error:
        logger.warning("chat notice failed: %r", error)
    await reporter.event(EventKind.NOTICE_POSTED, ok=posted)

    if settings.notice_audio:
        try:
            await adapter.set_microphone(page, True)
            player = await asyncio.create_subprocess_exec(
                "ffmpeg", "-nostdin", "-loglevel", "error", "-re", "-i", settings.notice_audio,
                "-f", "pulse", "-device", NOTICE_SINK, "notice",
            )  # fmt: skip
            spoken = await asyncio.wait_for(player.wait(), timeout=120) == 0
        except Exception as error:
            logger.warning("spoken notice failed: %r", error)
        finally:
            # A microphone left on would put the bot's silence, and any noise, into the call.
            with contextlib.suppress(Exception):
                await adapter.set_microphone(page, False)
        await reporter.event(EventKind.NOTICE_SPOKEN, ok=spoken)
    return posted or spoken
