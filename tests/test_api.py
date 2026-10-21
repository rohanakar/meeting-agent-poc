import uuid
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI
from rq.registry import ScheduledJobRegistry
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from meeting_agent.models import EMBEDDING_DIMENSIONS, Chunk
from meeting_agent.pipeline import embed
from meeting_agent.queues import BOTS_QUEUE, get_queue

MEET_URL = "https://meet.google.com/abc-defg-hij"
INTERNAL = {"X-Internal-Token": "test-internal-token"}


def create(client: TestClient, **overrides: object) -> dict:
    body = {"namespace": "acme", "meeting_url": MEET_URL, "title": "Weekly sync"} | overrides
    response = client.post("/api/meetings", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def unit_vector(index: int, other: int | None = None) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[index] = 1.0
    if other is not None:
        vector[other] = 0.5
    return vector


def test_create_meeting_queues_the_bot_now_or_schedules_it(client: TestClient) -> None:
    meeting = create(client, metadata={"matter": "M-1"})
    create(client, join_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat())

    assert meeting["platform"] == "google_meet"
    assert meeting["status"] == "scheduled"
    assert meeting["egress_mode"] == "direct"
    assert meeting["metadata"] == {"matter": "M-1"}
    [job] = get_queue(BOTS_QUEUE).jobs
    assert job.func_name == "meeting_agent.worker.jobs.run_bot"
    assert job.args == (meeting["id"],)
    assert len(ScheduledJobRegistry(queue=get_queue(BOTS_QUEUE))) == 1


def test_create_meeting_rejects_unknown_platforms(client: TestClient) -> None:
    response = client.post(
        "/api/meetings",
        json={
            "namespace": "acme",
            "meeting_url": "https://teams.microsoft.com/l/meetup-join/x",
            "title": "Teams call",
        },
    )

    assert response.status_code == 422
    assert "Google Meet" in response.json()["detail"]


def test_list_filters_by_namespace_status_platform_and_metadata(client: TestClient) -> None:
    first = create(client, title="One", metadata={"matter": "M-1"})
    create(client, title="Two", meeting_url="https://zoom.us/j/123456789")
    create(client, title="Other org", namespace="globex")

    acme = client.get("/api/meetings", params={"namespace": "acme"}).json()
    zoom = client.get("/api/meetings", params={"namespace": "acme", "platform": "zoom"}).json()
    matter = client.get(
        "/api/meetings", params={"namespace": "acme", "metadata": '{"matter": "M-1"}'}
    ).json()
    done = client.get("/api/meetings", params={"namespace": "acme", "status": "done"}).json()

    assert acme["total"] == 2
    assert [item["title"] for item in zoom["items"]] == ["Two"]
    assert [item["id"] for item in matter["items"]] == [first["id"]]
    assert done["total"] == 0


def test_bot_events_update_the_meeting(client: TestClient) -> None:
    meeting = create(client)
    url = f"/internal/meetings/{meeting['id']}/events"

    events = [
        {"kind": "egress_checked", "detail": {"http_ip": "203.0.113.7", "webrtc_ips": []}},
        {"kind": "waiting_admission"},
        {"kind": "admission", "detail": {"result": "admitted"}},
    ]
    for event in events:
        assert client.post(url, json=event, headers=INTERNAL).status_code == 204
    detail = client.get(f"/api/meetings/{meeting['id']}", params={"namespace": "acme"}).json()

    assert detail["meeting"]["exit_ip"] == "203.0.113.7"
    assert detail["meeting"]["admission"] == "admitted"
    assert detail["meeting"]["status"] == "in_meeting"
    assert [event["kind"] for event in detail["events"]] == [
        "egress_checked",
        "waiting_admission",
        "admission",
    ]


def test_stop_cancels_a_scheduled_meeting(client: TestClient) -> None:
    meeting = create(client)

    stopped = client.post(f"/api/meetings/{meeting['id']}/stop", params={"namespace": "acme"})
    again = client.post(f"/api/meetings/{meeting['id']}/stop", params={"namespace": "acme"})

    assert stopped.json()["status"] == "cancelled"
    assert get_queue(BOTS_QUEUE).jobs == []
    assert again.status_code == 409


def test_search_ranks_by_similarity_within_the_namespace(
    client: TestClient, db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    meeting = create(client)
    other = create(client, namespace="globex")
    with Session(db) as session:
        for namespace, meeting_id, kind, chunk_text, vector in [
            ("acme", meeting["id"], "transcript", "close", unit_vector(0)),
            ("acme", meeting["id"], "summary", "related", unit_vector(1)),
            ("acme", meeting["id"], "transcript", "far", unit_vector(2)),
            ("globex", other["id"], "transcript", "other org", unit_vector(0)),
        ]:
            session.add(
                Chunk(
                    namespace=namespace,
                    meeting_id=uuid.UUID(meeting_id),
                    kind=kind,
                    text=chunk_text,
                    embedding=vector,
                )
            )
        session.commit()

    # openai speaks httpx2, which respx does not patch, so the client gets a mock transport.
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": unit_vector(0, 1)}],
                "model": "text-embedding-3-small",
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
        )

    monkeypatch.setattr(
        embed,
        "OpenAI",
        lambda **client: OpenAI(
            **client, http_client=httpx2.Client(transport=httpx2.MockTransport(handler))
        ),
    )

    everything = client.get("/api/search", params={"namespace": "acme", "q": "budget"}).json()
    transcripts = client.get(
        "/api/search", params={"namespace": "acme", "q": "budget", "kind": "transcript"}
    ).json()

    assert [hit["text"] for hit in everything["items"]] == ["close", "related", "far"]
    assert everything["items"][0]["meeting_title"] == "Weekly sync"
    assert [hit["text"] for hit in transcripts["items"]] == ["close", "far"]
