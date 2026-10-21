"""The parts added to scripts/framework/mock_llm_server.py: embeddings and the API key check.

The Claude Code backend is not run here; the echo backend hashes each text's own words.
"""

import base64
import math
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mocks.anthropic.mock_llm_server import SessionBindings, create_app, hashed_vector


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("MOCK_API_KEY", "test")
    app = create_app(
        backend_name="echo",
        backend=None,
        dump_directory=None,
        bindings=SessionBindings(tmp_path / "none.json"),
    )
    return TestClient(app, headers={"Authorization": "Bearer test"})


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_hashed_vectors_are_unit_length_and_close_for_shared_terms() -> None:
    budget = hashed_vector(["budget", "cost", "finance"], 1536)
    costs = hashed_vector(["cost", "finance", "spending"], 1536)
    weather = hashed_vector(["rain", "forecast", "storm"], 1536)

    assert math.isclose(math.sqrt(sum(x * x for x in budget)), 1.0)
    assert hashed_vector(["budget", "cost", "finance"], 1536) == budget
    assert cosine(budget, costs) > 0.5 > cosine(budget, weather)


def test_embeddings_answer_in_the_openai_format(client: TestClient) -> None:
    response = client.post(
        "/v1/embeddings", json={"model": "text-embedding-3-small", "input": ["a b", "c"]}
    )

    body = response.json()
    assert [item["index"] for item in body["data"]] == [0, 1]
    assert len(body["data"][0]["embedding"]) == 1536
    assert body["usage"]["total_tokens"] > 0


def test_base64_embeddings_are_little_endian_float32(client: TestClient) -> None:
    plain = client.post("/v1/embeddings", json={"model": "m", "input": "budget"}).json()
    encoded = client.post(
        "/v1/embeddings", json={"model": "m", "input": "budget", "encoding_format": "base64"}
    ).json()

    decoded = struct.unpack("<1536f", base64.b64decode(encoded["data"][0]["embedding"]))
    assert decoded == pytest.approx(plain["data"][0]["embedding"], abs=1e-6)


def test_requests_need_the_mock_key(client: TestClient) -> None:
    wrong = client.post(
        "/v1/messages",
        json={"model": "m", "max_tokens": 10, "messages": []},
        headers={"Authorization": "", "x-api-key": "real-key"},
    )
    anthropic_style = client.post(
        "/v1/embeddings",
        json={"model": "m", "input": "x"},
        headers={"Authorization": "", "x-api-key": "test"},
    )

    assert wrong.status_code == 401
    assert anthropic_style.status_code == 200
    assert client.get("/__mock__/health", headers={"Authorization": ""}).status_code == 200
