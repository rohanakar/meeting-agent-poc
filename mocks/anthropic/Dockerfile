# mock_llm_server.py (from scripts/framework) with Claude Code as its only backend.
FROM node:22-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code@2.1.280 \
    && python3 -m venv /opt/mock \
    && /opt/mock/bin/pip install --no-cache-dir "fastapi>=0.110" "uvicorn>=0.30"

COPY mocks/anthropic/mock_llm_server.py /app/mock_llm_server.py
COPY mocks/anthropic/entrypoint.sh /usr/local/bin/mock-anthropic
# Claude Code keeps its state in the home folder of whoever runs it; node has one.
USER node
ENV DISABLE_AUTOUPDATER=1 MOCK_API_KEY=test
EXPOSE 11435
ENTRYPOINT ["mock-anthropic"]
