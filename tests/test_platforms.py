import pytest

from meeting_agent.platforms import Platform, UnsupportedMeetingUrl, detect_platform, join_url


@pytest.mark.parametrize(
    ("url", "platform"),
    [
        ("https://meet.google.com/abc-defg-hij", Platform.GOOGLE_MEET),
        ("https://meet.google.com/abc-defg-hij?authuser=1", Platform.GOOGLE_MEET),
        ("https://zoom.us/j/123456789?pwd=abc", Platform.ZOOM),
        ("https://us02web.zoom.us/j/12345678901", Platform.ZOOM),
        ("https://app.zoom.us/wc/join/123456789", Platform.ZOOM),
    ],
)
def test_detect_platform(url: str, platform: Platform) -> None:
    assert detect_platform(url) == platform


@pytest.mark.parametrize(
    "url",
    [
        "http://meet.google.com/abc-defg-hij",
        "https://meet.google.com/landing",
        "https://teams.microsoft.com/l/meetup-join/abc",
        "https://evilzoom.us/j/123456789",
    ],
)
def test_unsupported_urls(url: str) -> None:
    with pytest.raises(UnsupportedMeetingUrl):
        detect_platform(url)


def test_meet_join_url_drops_query() -> None:
    assert (
        join_url("https://meet.google.com/abc-defg-hij?authuser=1")
        == "https://meet.google.com/abc-defg-hij"
    )


def test_zoom_join_url_uses_web_client_and_keeps_passcode() -> None:
    assert (
        join_url("https://us02web.zoom.us/j/123456789?pwd=s3cret&uname=x")
        == "https://app.zoom.us/wc/join/123456789?pwd=s3cret"
    )
