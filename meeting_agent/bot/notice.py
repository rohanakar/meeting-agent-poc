import asyncio
import contextlib
import logging

from playwright.async_api import Page

from ..config import BotSettings
from ..enums import EventKind
from .adapters.base import MeetingAdapter, with_overlay_retry
from .report import Reporter

logger = logging.getLogger(__name__)

# Set up by docker/bot-entrypoint.sh: whatever plays here reaches Chromium's microphone, the
# bot_mic source.
NOTICE_SINK = "notice_in"
BOT_MIC = "bot_mic"


class NoticeNotHeard(Exception):
    """The clip would play, but nobody in the meeting would hear it."""


async def pactl_short(kind: str) -> list[list[str]]:
    process = await asyncio.create_subprocess_exec(
        "pactl", "list", "short", kind, stdout=asyncio.subprocess.PIPE
    )
    output, _ = await process.communicate()
    return [line.split("\t") for line in output.decode().splitlines() if line]


async def wait_until_browser_listens(timeout_s: float = 5) -> None:
    """Waits until Chromium records from bot_mic. Unmuting in the page opens the capture; if it
    never opens, the clip would go nowhere while the timeline said it was spoken."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        sources = {row[1]: row[0] for row in await pactl_short("sources") if len(row) > 1}
        listening = {row[1] for row in await pactl_short("source-outputs") if len(row) > 1}
        if sources.get(BOT_MIC) in listening:
            return
        if asyncio.get_running_loop().time() > deadline:
            raise NoticeNotHeard("the browser is not recording from the bot's microphone")
        await asyncio.sleep(0.5)


async def give_notice(
    adapter: MeetingAdapter, page: Page, settings: BotSettings, reporter: Reporter
) -> tuple[bool, bool]:
    """Tells the meeting it is being recorded: out loud first, then in chat.

    The spoken notice goes first, and by the time it ends the call page has settled, so the chat
    panel opens quickly. Returns whether the chat notice and the spoken notice went out. The bot
    records only when at least one did, and keeps trying the chat notice while it records
    (main.watch).
    """
    posted = spoken = False
    if settings.notice_audio:
        reason = None
        try:
            await adapter.set_microphone(page, True)
            await wait_until_browser_listens()
            player = await asyncio.create_subprocess_exec(
                "ffmpeg", "-nostdin", "-loglevel", "error", "-re", "-i", settings.notice_audio,
                "-f", "pulse", "-device", NOTICE_SINK, "notice",
            )  # fmt: skip
            spoken = await asyncio.wait_for(player.wait(), timeout=120) == 0
            reason = None if spoken else "the clip did not play"
        except Exception as error:
            logger.warning("spoken notice failed: %r", error)
            reason = str(error).splitlines()[0][:200] or type(error).__name__
        finally:
            # A microphone left on would put the bot's silence, and any noise, into the call.
            with contextlib.suppress(Exception):
                await adapter.set_microphone(page, False)
        if spoken:
            await reporter.event(EventKind.NOTICE_SPOKEN, ok=True)
        else:
            await reporter.event(EventKind.NOTICE_SPOKEN, ok=False, reason=reason)

    try:
        await with_overlay_retry(page, lambda: adapter.send_chat(page, settings.notice_text))
        posted = True
    except Exception as error:
        logger.warning("chat notice failed: %r", error)
    await reporter.event(EventKind.NOTICE_POSTED, ok=posted)
    return posted, spoken
