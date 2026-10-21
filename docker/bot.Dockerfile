FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
# Xvfb: Chromium runs headed, because meeting pages treat headless browsers as bots.
# PulseAudio: Chromium plays the meeting into a virtual sink that ffmpeg records.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb pulseaudio pulseaudio-utils ffmpeg dumb-init fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra bot --no-install-project \
    && playwright install --with-deps chromium
COPY meeting_agent ./meeting_agent
RUN uv sync --frozen --no-dev --extra bot

# uid 1000: the worker gives each meeting folder to this user before it starts the bot.
RUN useradd --uid 1000 --create-home bot
COPY docker/bot-entrypoint.sh /usr/local/bin/bot-entrypoint
USER bot
ENTRYPOINT ["dumb-init", "--", "bot-entrypoint"]
CMD ["python", "-m", "meeting_agent.bot.main"]
