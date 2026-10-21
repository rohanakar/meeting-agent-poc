"""Tests run against the compose Postgres and Redis (docker compose up -d postgres redis).

Settings are read once, so the environment is set before anything imports them.
"""

import os
import tempfile
from collections.abc import Iterator

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

TEST_DB = "meeting_agent_test"
os.environ.update(
    DATABASE_URL=f"postgresql+psycopg://meeting:meeting@localhost:5442/{TEST_DB}",
    REDIS_URL="redis://localhost:6392/15",
    API_KEY="test-api-key",
    INTERNAL_TOKEN="test-internal-token",
    OPENAI_API_KEY="test-openai-key",
    ANTHROPIC_API_KEY="test-anthropic-key",
    ELEVENLABS_API_KEY="test-elevenlabs-key",
    DATA_DIR=tempfile.mkdtemp(prefix="meeting-agent-test-"),
)

from meeting_agent.api.app import app  # noqa: E402
from meeting_agent.db import get_engine  # noqa: E402
from meeting_agent.queues import get_redis  # noqa: E402

TABLES = "action_items, bot_events, chunks, summaries, transcript_segments, meetings"


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    with psycopg.connect(
        "postgresql://meeting:meeting@localhost:5442/postgres", autocommit=True
    ) as connection:
        connection.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        connection.execute(f"CREATE DATABASE {TEST_DB}")
    command.upgrade(Config("alembic.ini"), "head")
    yield get_engine()
    get_engine().dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Engine]:
    yield engine
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {TABLES} CASCADE"))
    get_redis().flushdb()


@pytest.fixture
def client(db: Engine) -> TestClient:
    return TestClient(app, headers={"X-API-Key": "test-api-key"})
