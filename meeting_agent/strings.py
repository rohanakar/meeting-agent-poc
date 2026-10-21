"""Every fixed text the agent says, stores or looks for on a meeting page, in one place.

The API, the worker and the bot all import it, so it imports nothing but re.
"""

import re


def any_text(*phrases: str) -> re.Pattern[str]:
    """Matches any of the phrases, anywhere in a text, in any case."""
    return re.compile("|".join(re.escape(phrase) for phrase in phrases), re.IGNORECASE)


def either(*selectors: str) -> str:
    return ", ".join(selectors)


# What the bot is called and says. Both can be changed in .env (BOT_DISPLAY_NAME, NOTICE_TEXT).
BOT_DISPLAY_NAME = "Lexapar Notetaker (recording)"
NOTICE_TEXT = (
    "Hello, I am Lexapaar's AI notetaker. I will be recording this conversation to generate a "
    "summary. If anyone does not consent, please remove me from the call. "
)
# The bot is an attendee too, marked so nobody takes it for a person.
BOT_ATTENDEE = "{name} [BOT]"
# Names the bot makes up for tiles without a label; they count people but name nobody.
PLACEHOLDER_NAME = "Participant {number}"
PLACEHOLDER_NAME_PATTERN = re.compile(r"^Participant \d+$")

# Meeting links.
LINK_NOT_HTTPS = "The meeting link must start with https://"
LINK_NOT_SUPPORTED = (
    "Only Google Meet (meet.google.com/xxx-xxxx-xxx) and Zoom (zoom.us/j/<id>) links work"
)

# API errors.
WRONG_API_KEY = "Missing or wrong X-API-Key"
WRONG_INTERNAL_TOKEN = "Missing or wrong X-Internal-Token"
MEETING_NOT_FOUND = "Meeting not found"
MEETING_ALREADY_OVER = "The meeting is already {status}"
NO_RECORDING = "No recording for this meeting"
METADATA_NOT_JSON = "metadata must be JSON"
METADATA_QUERY_HELP = 'JSON object, e.g. {"matter":"X"}'

# Why a meeting failed, as the worker stores it.
NO_BOT_ACCOUNT = "No signed-in bot account: run docker compose --profile login up bot-login"
BOT_EXITED = "The bot exited with code {code}"
SILENT_RECORDING = "The recording is silent (max {volume} dB)"
NO_SPEECH = "No speech was found in the recording"
TRANSCRIPTION_FAILED = "Transcription failed: {error!r}"
PROCESSING_FAILED = "Processing failed: {error!r}"
UNEXPECTED_TRANSCRIPT = "Transcription returned a multichannel or deferred response"
NO_SUMMARY = "No summary (stop reason {reason})"
NO_PROXY_URL = "Egress mode is proxy, but no proxy URL is set (EGRESS_PROXY_URL)"

# What Claude gets besides the system prompt (prompts/summary.md).
SUMMARY_REQUEST = (
    "Meeting title: {title}\n"
    "People seen in the meeting: {people}\n\n"
    "<transcript>\n{transcript}\n</transcript>\n\n"
    "<chat>\n{chat}\n</chat>"
)
NOBODY_KNOWN = "not known"
NO_CHAT = "No messages."

# How the summary and action items read in search results.
DECISION_LINE = "Decision: {decision}"
OWNER_LINE = "Owner: {owner}"

# Tips and pop-ups that cover controls: only buttons with these labels are clicked, so no meeting
# control is ever pressed by accident. Not "Close": Meet's chat panel has one, and closing it
# made the chat notice fail on its retry.
DISMISS_BUTTON = re.compile(
    r"^(Got it|Dismiss|Not now|No thanks|OK|Skip|Maybe later|I understand)$", re.IGNORECASE
)
# Meet labels the account's own captions "You"; for the bot that is only its spoken notice.
CAPTIONS_SELF_LABEL = "you"

# Meet's labels as of October 2026, English UI (the browser is forced to en-US). Replace any of
# them in <selectors folder>/google_meet.json when Meet changes, without a rebuild.
MEET_SELECTORS: dict[str, str | re.Pattern[str]] = {
    "name_input": either('input[aria-label="Your name"]', 'input[placeholder="Your name"]'),
    # Without a camera Meet names it "Ask to join without camera", so only the start is matched.
    # Never "Switch here": it moves the account's session elsewhere into the bot.
    "join_button": re.compile(r"^(Ask to join|Join now|Join anyway)\b"),
    "no_devices_button": re.compile(r"Continue without microphone and camera"),
    "mic_off_button": either(
        '[role="button"][aria-label*="Turn off microphone"]',
        'button[aria-label*="Turn off microphone"]',
    ),
    "mic_on_button": either(
        '[role="button"][aria-label*="Turn on microphone"]',
        'button[aria-label*="Turn on microphone"]',
    ),
    "camera_off_button": either(
        '[role="button"][aria-label*="Turn off camera"]',
        'button[aria-label*="Turn off camera"]',
    ),
    "leave_button": 'button[aria-label="Leave call"]',
    "chat_button": 'button[aria-label^="Chat with everyone"]',
    "chat_input": either(
        'textarea[aria-label="Send a message"]', 'textarea[placeholder="Send a message"]'
    ),
    # One message, its text, the group of messages one person sent in a row, and the group's
    # header (sender name, then time; the time only, for the bot's own messages).
    "chat_message": "div[data-message-id]",
    "chat_message_text": '[jsname="dTKtvb"]',
    "chat_group": '[jsname="Ypafjf"]',
    "chat_sender": 'div:has(> [jsname="biJjHb"])',
    "chat_time": '[jsname="biJjHb"]',
    "participant_tile": "[data-participant-id]",
    "participant_name": "span.notranslate",
    # Only other people's tiles carry a control for their microphone ("You can't mute X's
    # microphone remotely", "Mute X's microphone"); the bot's own tile has none.
    "others_mic_control": re.compile(r"mute .+['’]s microphone", re.IGNORECASE),
    # Tiles that show a shared screen, not a person.
    "presentation_text": any_text("Presentation", "is presenting", "You are presenting"),
    "captions_button": re.compile(r"^Turn on captions"),
    "captions_region": '[role="region"][aria-label*="Captions" i]',
    "denied_text": any_text("denied your request to join"),
    "blocked_text": any_text("You can't join this call", "You can't join this video call"),
    "timeout_text": any_text("No one responded to your request"),
    "ended_text": any_text(
        "You've been removed from the meeting",
        "You left the meeting",
        "ended the meeting for everyone",
        "The call has ended",
        "This call has ended",
        "The meeting has ended",
        "Return to home screen",
    ),
}

# The Zoom web client draws the meeting either in the page or in an iframe with this id.
ZOOM_CLIENT_FRAME = "iframe#webclient"
# Zoom web client (app.zoom.us/wc) as of October 2026, English UI. Replace any of them in
# <selectors folder>/zoom.json when Zoom changes, without a rebuild.
ZOOM_SELECTORS: dict[str, str | re.Pattern[str]] = {
    "cookie_accept": "#onetrust-accept-btn-handler",
    "name_input": "#input-for-name",
    "agree_button": re.compile(r"^I Agree$"),
    "preview_mute_button": re.compile(r"^Mute$"),
    "preview_stop_video_button": re.compile(r"^Stop Video$"),
    "join_button": re.compile(r"^Join$"),
    "join_audio_button": re.compile(r"Join Audio by Computer"),
    "leave_button": 'button[aria-label="Leave"], button.footer__leave-btn',
    "leave_confirm_button": re.compile(r"^Leave Meeting$"),
    "chat_button": 'button[aria-label*="chat panel" i]',
    "chat_input": (
        '.chat-rtf-box__editor-outer [contenteditable="true"], textarea.chat-box__chat-textarea'
    ),
    # Not yet checked against a live Zoom meeting.
    "chat_message": '[class*="new-chat-message__text-box"]',
    "chat_message_text": '[class*="new-chat-message__text-content"]',
    "chat_group": '[class*="chat-item-container"]',
    "chat_sender": '[class*="chat-item__sender"]',
    "chat_time": '[class*="chat-item__chat-info-time"]',
    "unmute_button": 'button[aria-label="unmute my microphone"]',
    "mute_button": 'button[aria-label="mute my microphone"]',
    "participant_count": ".footer-button__number-counter",
    "denied_text": any_text("removed you from the waiting room", "You have been removed"),
    "blocked_text": any_text(
        "This meeting link is invalid", "Passcode wrong", "This meeting ID is not valid"
    ),
    "ended_text": any_text(
        "This meeting has been ended by host",
        "The meeting has been ended",
        "You have been removed from this meeting",
    ),
}
