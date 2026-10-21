#!/bin/sh
# Sets up a fresh Ubuntu 24.04 EC2 host (c7i.xlarge: 4 vCPU, 8 GiB, about 2 bots at once)
# and starts meeting-agent. Run from the repository folder as the default "ubuntu" user:
#
#   ./scripts/ec2_setup.sh
#
# Run it again after a git pull to rebuild and restart.
set -eu

if ! command -v docker >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y docker.io docker-compose-v2
    sudo usermod -aG docker "$USER"
fi

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env. Fill in the API keys (and Tailscale or proxy settings), then run again."
    exit 1
fi

# Tunnel mode needs the Tailscale container, which starts only with its compose profile.
profile=""
if grep -Eq '^TS_AUTHKEY=.+' .env; then
    profile="--profile tunnel"
fi

# shellcheck disable=SC2086
sudo docker compose $profile up -d --build

echo "meeting-agent runs on port 8040. Keep that port closed to the internet: allow only your"
echo "office address in the security group, or use: ssh -L 8040:localhost:8040 ubuntu@<host>"
