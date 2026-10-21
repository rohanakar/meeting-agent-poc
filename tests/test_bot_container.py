"""Runs the real bot image against tests/fake_meeting: Xvfb, PulseAudio, Chromium and ffmpeg.

Build the image first: docker compose build (or set BOT_IMAGE to another tag)
Needs internet access for the egress check.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import docker
import pytest
from docker.errors import ImageNotFound

from meeting_agent.config import get_settings
from meeting_agent.worker.launcher import BOT_COMMAND, BOT_ENTRYPOINT, BOT_USER

IMAGE = get_settings().bot_image
FAKE_MEETING = Path(__file__).parent / "fake_meeting" / "index.html"

pytestmark = pytest.mark.docker


@pytest.fixture(scope="module")
def docker_client() -> docker.DockerClient:
    client = docker.from_env()
    try:
        client.images.get(IMAGE)
    except ImageNotFound:
        pytest.skip(f"{IMAGE} is not built")
    return client


@pytest.fixture
def meeting_files(tmp_path: Path) -> Path:
    folder = tmp_path / "fake"
    folder.mkdir()
    shutil.copy(FAKE_MEETING, folder / "index.html")
    for name, frequency, seconds in [("clip.wav", 440, 6), ("notice.wav", 880, 1)]:
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
             f"sine=frequency={frequency}:duration={seconds}", str(folder / name)],
            check=True,
        )  # fmt: skip
    return folder


def run_bot(client: docker.DockerClient, meeting_files: Path, out: Path) -> int:
    out.mkdir()
    container = client.containers.run(
        IMAGE,
        BOT_COMMAND,
        entrypoint=BOT_ENTRYPOINT,
        user=BOT_USER,
        detach=True,
        shm_size="1g",
        volumes={
            str(meeting_files): {"bind": "/fake", "mode": "ro"},
            str(out): {"bind": "/out", "mode": "rw"},
        },
        environment={
            "BOT_MEETING_ID": "container-test",
            "BOT_PLATFORM": "fake",
            "BOT_JOIN_URL": "file:///fake/index.html",
            "BOT_DISPLAY_NAME": "Test Notetaker",
            "BOT_NOTICE_AUDIO": "/fake/notice.wav",
            "BOT_ADMISSION_TIMEOUT_S": "20",
            "BOT_OUT_DIR": "/out",
            # Nothing listens here: the bot must carry on when it cannot report.
            "BOT_API_URL": "http://127.0.0.1:9",
        },
    )
    try:
        status = container.wait(timeout=180)
        if status["StatusCode"] not in (0, 2):
            print(container.logs().decode())
        return status["StatusCode"]
    finally:
        container.remove(force=True)


def events(out: Path) -> list[dict]:
    return [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]


def max_volume_db(path: Path) -> float:
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(re.search(r"max_volume: (-?[\d.]+) dB", result.stderr).group(1))


def test_bot_gives_notice_then_records_the_meeting(
    docker_client: docker.DockerClient, meeting_files: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"

    exit_code = run_bot(docker_client, meeting_files, out)

    log = events(out)
    assert exit_code == 0
    assert [event["kind"] for event in log] == [
        "launched",
        "egress_checked",
        "joining",
        "waiting_admission",
        "admission",
        "notice_spoken",
        "notice_posted",
        "recording_started",
        "captions",
        "participants",
        "meeting_ended",
        "recording_saved",
    ]
    by_kind = {event["kind"]: event["detail"] for event in log}
    assert by_kind["admission"] == {"result": "admitted", "decided_by": "page"}
    assert by_kind["notice_posted"] == {"ok": True}
    assert by_kind["notice_spoken"] == {"ok": True}
    assert by_kind["participants"] == {"names": ["Host", "Test Notetaker"]}
    assert by_kind["meeting_ended"] == {"reason": "ended"}
    assert by_kind["egress_checked"]["leaked_ips"] == []
    assert by_kind["captions"] == {"on": True}
    assert json.loads((out / "participants.json").read_text()) == [
        {"name": "Host", "email": None},
        {"name": "Test Notetaker [BOT]", "email": None},
    ]
    # The fake captions name "Host" while the 6 s clip plays: about one mark per half second.
    marks = json.loads((out / "speaker_timeline.json").read_text())
    assert {mark["name"] for mark in marks} == {"Host"}
    assert len(marks) >= 6
    # Host's message is kept; the bot's own notice is not.
    assert [(m["sender"], m["text"]) for m in json.loads((out / "chat.json").read_text())] == [
        ("Host", "Budget draft: https://example.com/b")
    ]
    # The clip is a 440 Hz tone at about -18 dB; silence would be -91 dB.
    assert max_volume_db(out / "audio.ogg") > -30
