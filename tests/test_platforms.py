import pytest

from meeting_agent.platforms import Platform, UnsupportedMeetingUrl, detect_platform, join_url


def test_links_name_their_platform_and_the_page_the_bot_opens() -> None:
    meet = "https://meet.google.com/abc-defg-hij?authuser=1"
    zoom = "https://us02web.zoom.us/j/123456789?pwd=s3cret&uname=x"

    assert detect_platform(meet) == Platform.GOOGLE_MEET
    assert detect_platform(zoom) == Platform.ZOOM
    assert join_url(meet) == "https://meet.google.com/abc-defg-hij"
    # Zoom invite links open the desktop app, so the bot goes to the web client.
    assert join_url(zoom) == "https://app.zoom.us/wc/join/123456789?pwd=s3cret"
    for unsupported in ["http://meet.google.com/abc-defg-hij", "https://evilzoom.us/j/123456789"]:
        with pytest.raises(UnsupportedMeetingUrl):
            detect_platform(unsupported)
