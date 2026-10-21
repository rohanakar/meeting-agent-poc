"""Runs the real bot image against tests/fake_meeting: Xvfb, PulseAudio, Chromium and ffmpeg.

Build the image first: docker compose build
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

from meeting_agent.worker.launcher import BOT_COMMAND, BOT_ENTRYPOINT, BOT_USER

IMAGE = "meeting-agent:latest"
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


def run_bot(client: docker.DockerClient, meeting_files: Path, out: Path, query: str = "") -> int:
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
            "BOT_JOIN_URL": f"file:///fake/index.html{query}",
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
        "notice_posted",
        "notice_spoken",
        "recording_started",
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
    assert json.loads((out / "participants.json").read_text()) == ["Host", "Test Notetaker"]
    # The clip is a 440 Hz tone at about -18 dB; silence would be -91 dB.
    assert max_volume_db(out / "audio.ogg") > -30


def test_bot_does_not_record_when_denied(
    docker_client: docker.DockerClient, meeting_files: Path, tmp_path: Path
) -> None:
    out = tmp_path / "out"

    exit_code = run_bot(docker_client, meeting_files, out, query="?deny")

    log = events(out)
    assert exit_code == 2
    assert log[-2]["detail"] == {"result": "denied", "decided_by": "page"}
    assert (log[-1]["kind"], log[-1]["detail"]) == ("failed", {"reason": "admission_denied"})
    assert not (out / "audio.ogg").exists()
    assert (out / "debug" / "last-page.png").exists()
