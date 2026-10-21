"""One bot, one meeting: join, give notice, record, leave. Runs inside the bot container.

The platform adapter reads the page; the media connection is read too, and wins when the page
has changed: media flowing means admitted, media gone means the meeting is over, and long
silence means nobody is left.

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
from .adapters.base import load_selectors, with_overlay_retry
from .browser import launch
from .egress import check_egress
from .media import media_state
from .notice import give_notice
from .recorder import Recorder
from .report import Reporter

logger = logging.getLogger("meeting_agent.bot")

POLL_S = 5
# Media connections can drop for a moment and come back (network change, ICE restart).
MEDIA_LOST_S = 20
# Sound energy (WebRTC totalAudioEnergy) one poll must add to count as someone speaking:
# about -40 dBFS on average over the poll; background hiss stays far below it.
SPEECH_ENERGY_PER_POLL = 1e-3


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


async def admission_by_media(page: Page) -> Admission:
    """Admitted as soon as meeting audio arrives, whatever the page shows."""
    while not (await media_state(page)).receiving_audio:
        await asyncio.sleep(1)
    return Admission.ADMITTED


async def wait_for_admission(
    adapter: MeetingAdapter, page: Page, timeout_s: int
) -> tuple[Admission, str]:
    """The first answer from the page or from the media connection, and which one gave it."""
    by_page = asyncio.ensure_future(adapter.wait_for_admission(page, timeout_s))
    by_media = asyncio.ensure_future(admission_by_media(page))
    done, pending = await asyncio.wait({by_page, by_media}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    if by_page in done:
        return by_page.result(), "page"
    return by_media.result(), "media"


async def watch(
    adapter: MeetingAdapter,
    page: Page,
    settings: BotSettings,
    stop: asyncio.Event,
    reporter: Reporter,
    seen: set[str],
) -> str:
    """Returns why the meeting ended for the bot."""
    started = last_sound = time.monotonic()
    alone_since: float | None = None
    media_lost_since: float | None = None
    last_energy: float | None = None
    while True:
        now = time.monotonic()
        if stop.is_set():
            return "stopped"
        if reason := await adapter.ended_reason(page):
            return reason

        media = await media_state(page)
        if media.connections or media_lost_since is not None:
            # All connections closed or down: removed, or the meeting ended, whatever the page
            # says. Only once there were connections: a page without WebRTC reports none.
            if media.connected:
                media_lost_since = None
            else:
                media_lost_since = media_lost_since or now
                if now - media_lost_since > MEDIA_LOST_S:
                    return "media_lost"
            if last_energy is None or media.audio_energy - last_energy > SPEECH_ENERGY_PER_POLL:
                last_sound = now
            last_energy = media.audio_energy
            if now - last_sound > settings.silence_timeout_s:
                return "silence"

        names = await adapter.participant_names(page)
        if set(names) - seen:
            seen.update(names)
            await reporter.event(EventKind.PARTICIPANTS, names=sorted(seen))
        # One participant is the bot itself. An empty list means the page did not say, which
        # leaves the timer as it is: it used to restart it, and the bot then never left.
        if len(names) == 1:
            alone_since = alone_since or now
            if now - alone_since > settings.alone_timeout_s:
                return "alone"
        elif names:
            alone_since = None

        if now - started > settings.max_meeting_s:
            return "max_duration"
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_S)
        except TimeoutError:
            pass


async def run(settings: BotSettings) -> int:
    out_dir = settings.out_dir
    (out_dir / "debug").mkdir(parents=True, exist_ok=True)
    reporter = Reporter(settings.api_url, settings.meeting_id, settings.internal_token, out_dir)
    adapter_class = ADAPTERS[settings.platform]
    adapter = adapter_class(
        load_selectors(settings.selectors_dir, settings.platform, adapter_class.DEFAULT_SELECTORS)
    )
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
                signed_in=bool(settings.profile_dir),
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
                asyncio.ensure_future(
                    with_overlay_retry(page, lambda: adapter.join(page, settings.display_name))
                ),
                stop,
            )
            await reporter.event(EventKind.WAITING_ADMISSION)
            admission, decided_by = await until_stopped(
                asyncio.ensure_future(
                    wait_for_admission(adapter, page, settings.admission_timeout_s)
                ),
                stop,
            )
            await reporter.event(EventKind.ADMISSION, result=admission, decided_by=decided_by)
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
