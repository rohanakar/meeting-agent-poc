"""Which platform a meeting link belongs to, and the page the bot opens for it.

Kept free of browser code so that the API can validate links without Playwright.
"""

import re
from enum import StrEnum
from urllib.parse import parse_qs, urlencode, urlsplit


class Platform(StrEnum):
    GOOGLE_MEET = "google_meet"
    ZOOM = "zoom"


MEET_PATH = re.compile(r"^/[a-z]{3}-[a-z]{4}-[a-z]{3}$")
# /j/<id> is the invite link, /wc/join/<id> and /wc/<id>/join are web-client links.
ZOOM_PATH = re.compile(r"^/(?:j|wc/join|wc)/(\d{9,11})(?:/join)?$")


class UnsupportedMeetingUrl(ValueError):
    pass


def detect_platform(url: str) -> Platform:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        raise UnsupportedMeetingUrl("The meeting link must start with https://")
    if host == "meet.google.com" and MEET_PATH.match(parts.path):
        return Platform.GOOGLE_MEET
    if (host == "zoom.us" or host.endswith(".zoom.us")) and ZOOM_PATH.match(parts.path):
        return Platform.ZOOM
    raise UnsupportedMeetingUrl(
        "Only Google Meet (meet.google.com/xxx-xxxx-xxx) and Zoom (zoom.us/j/<id>) links work"
    )


def join_url(url: str) -> str:
    """The page the bot opens. Zoom invite links open the desktop app, so use the web client."""
    parts = urlsplit(url.strip())
    match detect_platform(url):
        case Platform.GOOGLE_MEET:
            return f"https://meet.google.com{parts.path}"
        case Platform.ZOOM:
            meeting_id = ZOOM_PATH.match(parts.path).group(1)  # type: ignore[union-attr]
            passcode = parse_qs(parts.query).get("pwd")
            query = f"?{urlencode({'pwd': passcode[0]})}" if passcode else ""
            return f"https://app.zoom.us/wc/join/{meeting_id}{query}"
