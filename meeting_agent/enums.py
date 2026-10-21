# Shared by the API, the worker and the bot container, so it imports nothing heavy.
from enum import StrEnum


class MeetingStatus(StrEnum):
    SCHEDULED = "scheduled"
    JOINING = "joining"
    WAITING = "waiting"
    IN_MEETING = "in_meeting"
    TRANSCRIBING = "transcribing"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {MeetingStatus.DONE, MeetingStatus.FAILED, MeetingStatus.CANCELLED}


class EgressMode(StrEnum):
    DIRECT = "direct"
    PROXY = "proxy"


class Admission(StrEnum):
    ADMITTED = "admitted"
    DENIED = "denied"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"


class ChunkKind(StrEnum):
    TRANSCRIPT = "transcript"
    SUMMARY = "summary"
    ACTION_ITEM = "action_item"


class EventKind(StrEnum):
    LAUNCHED = "launched"
    EGRESS_CHECKED = "egress_checked"
    JOINING = "joining"
    WAITING_ADMISSION = "waiting_admission"
    ADMISSION = "admission"
    NOTICE_POSTED = "notice_posted"
    NOTICE_SPOKEN = "notice_spoken"
    RECORDING_STARTED = "recording_started"
    CAPTIONS = "captions"
    PARTICIPANTS = "participants"
    MEETING_ENDED = "meeting_ended"
    RECORDING_SAVED = "recording_saved"
    AUDIO_SILENT = "audio_silent"
    TRANSCRIBED = "transcribed"
    PROCESSED = "processed"
    FAILED = "failed"
