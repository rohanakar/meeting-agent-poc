"""One bot, one meeting: join, give notice, record, leave. Runs inside the bot container.

Exit codes: 0 the meeting was recorded, 2 the bot did not record (not admitted, egress leak,
no notice, stopped first), 1 the bot failed. A partial recording is kept in every case.
"""

import asyncio
import json
import logging
import signal
import sys
import time

from playwright.async_api import Page, async_playwright

from ..config import BotSettings
from ..enums import Admission, EgressMode, EventKind
from .adapters import ADAPTERS, MeetingAdapter
from .browser import launch
from .egress import check_egress
from .notice import give_notice
from .recorder import Recorder
from .report import Reporter

logger = logging.getLogger("meeting_agent.bot")

POLL_S = 5


class NotRecorded(Exception):
    """An expected way for a run to end without a recording, such as not being admitted."""


async def until_stopped[T](work: asyncio.Future[T] | asyncio.Task[T], stop: asyncio.Event) -> T:
    """Waits for work, unless the bot is told to stop first."""
    stopper = asyncio.ensure_future(stop.wait())
    done, _ = await asyncio.wait({work, stopper}, return_when=asyncio.FIRST_COMPLETED)
    if work not in done:
        work.cancel()
        raise NotRecorded("stopped")
    stopper.cancel()
    return work.result()


async def watch(
    adapter: MeetingAdapter,
    page: Page,
    settings: BotSettings,
    stop: asyncio.Event,
    reporter: Reporter,
    seen: set[str],
) -> str:
    """Returns why the meeting ended for the bot."""
    started = time.monotonic()
    alone_since: float | None = None
    while True:
        if stop.is_set():
            return "stopped"
        if reason := await adapter.ended_reason(page):
            return reason
        names = await adapter.participant_names(page)
        if set(names) - seen:
            seen.update(names)
            await reporter.event(EventKind.PARTICIPANTS, names=sorted(seen))
        # One participant is the bot itself. An empty list means the page did not say.
        if len(names) == 1:
            alone_since = alone_since or time.monotonic()
            if time.monotonic() - alone_since > settings.alone_timeout_s:
                return "alone"
        else:
            alone_since = None
        if time.monotonic() - started > settings.max_meeting_s:
            return "max_duration"
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_S)
        except TimeoutError:
            pass


async def run(settings: BotSettings) -> int:
    out_dir = settings.out_dir
    (out_dir / "debug").mkdir(parents=True, exist_ok=True)
    reporter = Reporter(settings.api_url, settings.meeting_id, settings.internal_token, out_dir)
    adapter = ADAPTERS[settings.platform]()
    stop = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(signum, stop.set)

    recorder = Recorder(out_dir / "audio.ogg")
    seen: set[str] = set()
    page: Page | None = None
    exit_code = 0
    async with async_playwright() as playwright:
        context = await launch(playwright, settings)
        try:
            await reporter.event(
                EventKind.LAUNCHED,
                platform=settings.platform,
                egress_mode=settings.egress_mode,
                signed_in=bool(settings.account_state_file),
            )
            egress = await check_egress(context)
            await reporter.event(
                EventKind.EGRESS_CHECKED,
                http_ip=egress.http_ip,
                webrtc_ips=egress.webrtc_ips,
                leaked_ips=egress.leaked_ips,
            )
            if egress.leaked_ips and settings.egress_mode != EgressMode.DIRECT:
                raise NotRecorded("egress_leak")

            page = await context.new_page()
            await reporter.event(EventKind.JOINING)
            await page.goto(settings.join_url)
            await until_stopped(
                asyncio.ensure_future(adapter.join(page, settings.display_name)), stop
            )
            await reporter.event(EventKind.WAITING_ADMISSION)
            admission = await until_stopped(
                asyncio.ensure_future(
                    adapter.wait_for_admission(page, settings.admission_timeout_s)
                ),
                stop,
            )
            await reporter.event(EventKind.ADMISSION, result=admission)
            if admission != Admission.ADMITTED:
                raise NotRecorded(f"admission_{admission}")

            # Notice first: nothing is recorded before the meeting has been told.
            if not await give_notice(adapter, page, settings, reporter):
                raise NotRecorded("notice_failed")
            recorder.start()
            await reporter.event(EventKind.RECORDING_STARTED)
            reason = await watch(adapter, page, settings, stop, reporter, seen)
            await reporter.event(EventKind.MEETING_ENDED, reason=reason)
        except NotRecorded as outcome:
            exit_code = 2
            await reporter.event(EventKind.FAILED, reason=str(outcome))
        except Exception as error:
            exit_code = 1
            logger.exception("bot failed")
            await reporter.event(EventKind.FAILED, reason="bot_error", error=repr(error)[:500])
        finally:
            seconds = recorder.stop()
            audio = out_dir / "audio.ogg"
            if audio.exists():
                await reporter.event(
                    EventKind.RECORDING_SAVED, seconds=round(seconds), bytes=audio.stat().st_size
                )
            (out_dir / "participants.json").write_text(json.dumps(sorted(seen)))
            if page is not None and not page.is_closed():
                # Kept for every run that did not end well: bots often fail with a page that
                # looks fine to the code, and the screenshot shows what the bot saw.
                try:
                    if exit_code:
                        await page.screenshot(path=out_dir / "debug" / "last-page.png")
                        (out_dir / "debug" / "last-page.html").write_text(await page.content())
                    await adapter.leave(page)
                except Exception:
                    logger.warning("could not save the page or leave cleanly", exc_info=True)
            await context.close()
            await reporter.aclose()
    return exit_code


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    sys.exit(asyncio.run(run(BotSettings())))


if __name__ == "__main__":
    main()
