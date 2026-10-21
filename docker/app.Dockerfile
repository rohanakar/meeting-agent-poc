FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
# ffmpeg: the worker checks recordings for silence.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY migrations ./migrations
COPY prompts ./prompts
COPY meeting_agent ./meeting_agent
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
