import shutil
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from meeting_agent.config import get_settings, meeting_dir
from meeting_agent.models import Meeting
from meeting_agent.queues import PIPELINE_QUEUE, get_queue
from meeting_agent.worker import jobs, launcher

TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/"


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
            "identity": "anonymous",
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
def tts() -> respx.Route:
    with respx.mock:
        yield respx.post(url__startswith=TTS_URL).mock(
            return_value=httpx.Response(200, content=b"ID3 notice")
        )


def test_run_bot_starts_the_container_and_queues_transcription(
    db: Engine, monkeypatch: pytest.MonkeyPatch, tts: respx.Route
) -> None:
    meeting = add_meeting(db)
    fake = fake_docker(monkeypatch, exit_code=0, records=True)

    jobs.run_bot(str(meeting.id))

    [run] = fake.runs
    environment = run["environment"]
    assert run["image"] == "meeting-agent:latest"
    assert run["command"] == ["bot-entrypoint", "python", "-m", "meeting_agent.bot.main"]
    assert run["user"] == "bot"
    assert run["network"] == "meeting-agent"
    assert "network_mode" not in run
    assert environment["BOT_JOIN_URL"] == "https://meet.google.com/abc-defg-hij"
    assert environment["BOT_PROXY_URL"] == ""
    assert environment["BOT_OUT_DIR"] == str(meeting_dir(meeting.id))
    assert open(environment["BOT_NOTICE_AUDIO"], "rb").read() == b"ID3 notice"
    assert reload(db, meeting).status == "transcribing"
    assert reload(db, meeting).container_id == "container-1"
    [job] = get_queue(PIPELINE_QUEUE).jobs
    assert job.func_name == "meeting_agent.worker.jobs.transcribe"
    assert (meeting_dir(meeting.id) / "bot.log").read_bytes() == b"bot log"


def test_tunnel_mode_puts_the_bot_in_the_tailscale_network(
    db: Engine, monkeypatch: pytest.MonkeyPatch, tts: respx.Route
) -> None:
    meeting = add_meeting(db, egress_mode="tunnel")
    fake = fake_docker(monkeypatch, exit_code=0, records=True)

    jobs.run_bot(str(meeting.id))

    [run] = fake.runs
    assert run["network_mode"] == "container:meeting-agent-tailscale"
    assert "network" not in run


def test_proxy_mode_gives_the_bot_the_proxy(
    db: Engine, monkeypatch: pytest.MonkeyPatch, tts: respx.Route
) -> None:
    meeting = add_meeting(db, egress_mode="proxy")
    monkeypatch.setattr(get_settings(), "egress_proxy_url", "http://user:pw@proxy.test:3128")
    fake = fake_docker(monkeypatch, exit_code=0, records=True)

    jobs.run_bot(str(meeting.id))

    assert fake.runs[0]["environment"]["BOT_PROXY_URL"] == "http://user:pw@proxy.test:3128"


def test_a_bot_that_did_not_record_fails_the_meeting(
    db: Engine, monkeypatch: pytest.MonkeyPatch, tts: respx.Route
) -> None:
    meeting = add_meeting(db, failure_reason="admission_denied")
    fake_docker(monkeypatch, exit_code=2, records=False)

    jobs.run_bot(str(meeting.id))

    assert reload(db, meeting).status == "failed"
    assert reload(db, meeting).failure_reason == "admission_denied"
    assert get_queue(PIPELINE_QUEUE).jobs == []


def test_a_cancelled_meeting_starts_no_bot(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    meeting = add_meeting(db, status="cancelled")
    fake = fake_docker(monkeypatch, exit_code=0, records=True)

    jobs.run_bot(str(meeting.id))

    assert fake.runs == []


def test_signed_in_needs_a_saved_account(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    meeting = add_meeting(db, identity="signed_in")
    fake = fake_docker(monkeypatch, exit_code=0, records=True)

    jobs.run_bot(str(meeting.id))

    assert fake.runs == []
    assert reload(db, meeting).status == "failed"
    assert "bot-login" in reload(db, meeting).failure_reason


def test_the_spoken_notice_is_made_once(db: Engine, tts: respx.Route) -> None:
    shutil.rmtree(get_settings().data_dir / "notice", ignore_errors=True)

    first = jobs.notice_audio(get_settings())
    second = jobs.notice_audio(get_settings())

    assert first == second
    assert tts.call_count == 1
