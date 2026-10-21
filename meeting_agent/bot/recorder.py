import signal
import subprocess
import time
from pathlib import Path

# Set up by docker/bot-entrypoint.sh: the sink Chromium plays the meeting into.
MEETING_MONITOR = "meeting_out.monitor"


class Recorder:
    """Records what the meeting plays, as mono 16 kHz Opus: enough for speech, ~14 MB an hour."""

    def __init__(self, path: Path):
        self.path = path
        self.process: subprocess.Popen[bytes] | None = None
        self.started_at = 0.0

    def start(self) -> None:
        self.started_at = time.monotonic()
        self.process = subprocess.Popen(
            [
                "ffmpeg", "-nostdin", "-loglevel", "error", "-y",
                "-f", "pulse", "-i", MEETING_MONITOR,
                "-ac", "1", "-ar", "16000",
                "-c:a", "libopus", "-b:a", "32k", "-application", "voip",
                str(self.path),
            ]
        )  # fmt: skip

    def stop(self) -> float:
        """Stops the recording and returns its length in seconds."""
        if self.process is None:
            return 0.0
        # SIGINT, not SIGTERM: ffmpeg then writes the Ogg trailer, so the file stays playable.
        self.process.send_signal(signal.SIGINT)
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process = None
        return time.monotonic() - self.started_at
