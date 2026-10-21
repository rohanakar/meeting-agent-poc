# The one meeting-agent image: API, workers, and the bot the worker starts for each meeting.

FROM node:22-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui ./
RUN npm run build

FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
# Xvfb: the bot runs Chromium headed, because meeting pages treat headless browsers as bots.
# PulseAudio: Chromium plays the meeting into a virtual sink that ffmpeg records.
# ffmpeg: also the worker's silence check.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb pulseaudio pulseaudio-utils ffmpeg dumb-init fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra bot --no-install-project \
    && playwright install --with-deps chromium
COPY alembic.ini ./
COPY migrations ./migrations
COPY prompts ./prompts
COPY meeting_agent ./meeting_agent
COPY --from=ui /ui/dist ./ui/dist
RUN uv sync --frozen --no-dev --extra bot

# The bot runs as uid 1000; the worker gives each meeting folder to this user.
RUN useradd --uid 1000 --create-home bot
COPY docker/bot-entrypoint.sh /usr/local/bin/bot-entrypoint

ENTRYPOINT ["dumb-init", "--"]
CMD ["uvicorn", "meeting_agent.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
