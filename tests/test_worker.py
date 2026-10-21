from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from meeting_agent.config import get_settings, meeting_dir
from meeting_agent.models import Meeting
from meeting_agent.queues import PIPELINE_QUEUE, get_queue
from meeting_agent.worker import jobs, launcher


class FakeContainer:
    id = "container-1"

    def __init__(self, exit_code: int, on_wait: Callable[[], None]):
        self.exit_code = exit_code
        self.on_wait = on_wait

    def wait(self, timeout: int | None = None) -> dict[str, int]:
        self.on_wait()
        return {"StatusCode": self.exit_code}

    def logs(self, tail: int) -> bytes:
        return b"bot log"

    def remove(self, force: bool) -> None:
        pass


class FakeDocker:
    """Stands in for docker.from_env(): records how the bot container would be started."""

    def __init__(self, container: FakeContainer):
        self.container = container
        self.runs: list[dict[str, Any]] = []
        self.containers = self

    def run(self, image: str, command: list[str], **kwargs: Any) -> FakeContainer:
        self.runs.append({"image": image, "command": command, **kwargs})
        return self.container


def add_meeting(db: Engine, **overrides: Any) -> Meeting:
    with Session(db, expire_on_commit=False) as session:
        fields = {
            "namespace": "acme",
            "platform": "google_meet",
            "meeting_url": "https://meet.google.com/abc-defg-hij",
            "title": "Weekly sync",
            "egress_mode": "direct",
            "meta": {},
        }
        meeting = Meeting(**fields | overrides)
        session.add(meeting)
        session.commit()
        return meeting


def reload(db: Engine, meeting: Meeting) -> Meeting:
    with Session(db, expire_on_commit=False) as session:
        return session.get(Meeting, meeting.id)


def fake_docker(monkeypatch: pytest.MonkeyPatch, exit_code: int, records: bool) -> FakeDocker:
    def on_wait() -> None:
        if records:
            (meeting_dir(fake.runs[0]["environment"]["BOT_MEETING_ID"]) / "audio.ogg").write_bytes(
                b"OggS"
            )

    fake = FakeDocker(FakeContainer(exit_code, on_wait))
    monkeypatch.setattr(launcher.docker, "from_env", lambda: fake)
    return fake


@pytest.fixture
def account(monkeypatch: pytest.MonkeyPatch) -> Path:
    """The signed-in profile bot-login would have made."""
    settings = get_settings()
    monkeypatch.setattr(settings, "bot_account_label", "notetaker")
    profile = settings.data_dir / "accounts" / "notetaker" / "profile"
    (profile / "Default").mkdir(parents=True, exist_ok=True)
    return profile


def test_run_bot_starts_the_signed_in_bot_and_queues_transcription(
    db: Engine, monkeypatch: pytest.MonkeyPatch, account: Path
) -> None:
    meeting = add_meeting(db, egress_mode="proxy")
    monkeypatch.setattr(get_settings(), "egress_proxy_url", "http://user:pw@proxy.test:3128")
    fake = fake_docker(monkeypatch, exit_code=0, records=True)

    jobs.run_bot(str(meeting.id))

    [run] = fake.runs
    environment = run["environment"]
    assert run["image"] == "meeting-agent:latest"
    assert run["command"] == ["bot-entrypoint", "python", "-m", "meeting_agent.bot.main"]
    assert run["user"] == "bot"
    assert run["network"] == "meeting-agent"
    assert environment["BOT_JOIN_URL"] == "https://meet.google.com/abc-defg-hij"
    assert environment["BOT_PROFILE_DIR"] == str(account)
    assert environment["BOT_PROXY_URL"] == "http://user:pw@proxy.test:3128"
    assert environment["BOT_OUT_DIR"] == str(meeting_dir(meeting.id))
    assert environment["BOT_NOTICE_AUDIO"].endswith("meeting_agent/bot/disclaimer.mp3")
    assert reload(db, meeting).status == "transcribing"
    assert reload(db, meeting).container_id == "container-1"
    [job] = get_queue(PIPELINE_QUEUE).jobs
    assert job.func_name == "meeting_agent.worker.jobs.transcribe"
    assert (meeting_dir(meeting.id) / "bot.log").read_bytes() == b"bot log"


def test_a_bot_that_did_not_record_fails_the_meeting(
    db: Engine, monkeypatch: pytest.MonkeyPatch, account: Path
) -> None:
    meeting = add_meeting(db, failure_reason="admission_denied")
    fake_docker(monkeypatch, exit_code=2, records=False)

    jobs.run_bot(str(meeting.id))

    assert reload(db, meeting).status == "failed"
    assert reload(db, meeting).failure_reason == "admission_denied"
    assert get_queue(PIPELINE_QUEUE).jobs == []


def test_without_a_signed_in_account_no_bot_starts(
    db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "bot_account_label", "")
    meeting = add_meeting(db)
    fake = fake_docker(monkeypatch, exit_code=0, records=True)

    jobs.run_bot(str(meeting.id))

    assert fake.runs == []
    assert reload(db, meeting).status == "failed"
    assert "bot-login" in reload(db, meeting).failure_reason
