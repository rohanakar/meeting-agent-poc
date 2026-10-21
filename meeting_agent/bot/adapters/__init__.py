from ...platforms import Platform
from .base import MeetingAdapter
from .fake import FakeMeeting
from .google_meet import GoogleMeet
from .zoom_web import ZoomWeb

# "fake" is not a Platform: the API never accepts it, only the container test uses it.
ADAPTERS: dict[str, type[MeetingAdapter]] = {
    Platform.GOOGLE_MEET: GoogleMeet,
    Platform.ZOOM: ZoomWeb,
    "fake": FakeMeeting,
}
