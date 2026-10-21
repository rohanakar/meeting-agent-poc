#!/bin/bash
# Deploys meeting-agent on the OCI host, from the Jenkins workspace checked out there:
#
#   ssh opc@<host> bash <workspace>/deploy/oci.sh <workspace>
#
# The host needs a one-time setup first (README, "Deploying on OCI"); this script only checks it.
set -euo pipefail
cd "$1"

SECRETS_FILE="$HOME/secrets/meeting-agent.env"
COMPOSE="$HOME/tools/docker-compose-v2.29.7"
fail() { echo "$1 See README, \"Deploying on OCI\"." >&2; exit 1; }
[ -f "$SECRETS_FILE" ] || fail "Create $SECRETS_FILE first."
[ -x "$COMPOSE" ] || fail "Install Docker Compose v2.29.7 as $COMPOSE first."
docker network inspect shared_net >/dev/null 2>&1 \
    || fail "Create the Docker network shared_net and attach Nginx Proxy Manager to it first."

# Compose reads .env: this server's settings, then the secrets.
umask 077
{
    cat <<'SETTINGS'
COMPOSE_FILE=docker-compose.yml:docker-compose.server.yml
MAX_CONCURRENT_BOTS=2
DEFAULT_EGRESS_MODE=direct
BOT_ACCOUNT_LABEL=notetaker
BOT_DISPLAY_NAME=Lexapar Notetaker (recording)
SETTINGS
    cat "$SECRETS_FILE"
} > .env

# --wait returns once the API's healthcheck passes, so one check through the sidecar is enough.
"$COMPOSE" up -d --build --remove-orphans --wait
"$COMPOSE" exec -T meeting-agent-web wget -qO- http://127.0.0.1:8000/api/health && echo
"$COMPOSE" exec -T api test -d /data/accounts/notetaker/profile \
    || echo "Note: the bot account is not signed in yet (README, \"Bot account\")."
docker image prune -f
