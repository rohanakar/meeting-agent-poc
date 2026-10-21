#!/bin/sh
# Answers every request with Claude Code, logged in with a token from `claude setup-token`.
set -eu
if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    echo "mock-anthropic answers with Claude Code and needs CLAUDE_CODE_OAUTH_TOKEN." >&2
    echo "Run 'claude setup-token' on your machine and put the token in .env." >&2
    exit 1
fi
exec /opt/mock/bin/python /app/mock_llm_server.py --host 0.0.0.0 --port 11435 --backend claude "$@"
