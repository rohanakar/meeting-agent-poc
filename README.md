# meeting-agent

A proof of concept: an agent that joins an online meeting, says that it records, records the audio and reads the chat, turns the audio into a transcript and writes a summary with action items. Transcripts, summaries and action items go into Postgres with pgvector, so they can be searched across meetings, one organisation at a time.

```
Invite → bot joins in a browser → notice out loud, then in chat → record + read chat → speech to text → summary and action items → search
```

It works on **Google Meet** and the **Zoom web client**. Each platform is one adapter file; egress, notice, recording and search are shared.

> **Consent.** Use the agent only in meetings where every participant agrees to the recording. It is not for meetings with opposing counsel, witnesses or clients who have not agreed (NYC Bar opinions 2025-6 and 2026-2). The bot announces itself before it records, and it does not record if it cannot announce.

## How it works

```
 UI / API ──POST /api/meetings──► Postgres (meetings)
                │
                └─► RQ queue "bots" ──► worker-bots ──docker run──► bot container
                                                                   (Xvfb, PulseAudio,
                                                                    Chromium, ffmpeg)
                                          events ◄── POST /internal/meetings/{id}/events
                                                                         │
            audio.ogg, chat.json, participants.json on the shared volume ◄─┘
                │
                └─► RQ queue "pipeline" ──► transcribe (ElevenLabs Scribe v2)
                                         └► process (Claude summary + OpenAI embeddings → pgvector)
```

1. **Invite.** Paste a meeting link in the UI (or call the API), with a title, the organisation and "now" or a start time. The platform comes from the link.
2. **Join.** `run_bot` starts one bot container from a copy of the signed-in bot account (see [Bot account](#bot-account)). The bot checks its egress, opens the meeting in headed Chromium, turns its camera and microphone off, and asks to join.
3. **Admission.** The bot waits up to 10 minutes. The outcome (`admitted`, `denied`, `timeout`, `blocked`) is stored on the meeting.
4. **Notice.** It plays `data/notice/disclaimer.mp3` through a virtual microphone, then posts `NOTICE_TEXT` in chat. If neither works, it leaves without recording; a chat notice that failed is retried every 30 s while it records.
5. **Record.** ffmpeg records what the meeting plays, as mono 16 kHz Opus (about 14 MB an hour). Every 5 s the bot also reads the chat panel and keeps each new message with its sender and time (`chat.json`); its own messages are left out. It stops when the meeting ends, when it has been alone for 30 seconds, after 3 hours, or when someone presses **Stop agent**.
6. **Speech to text.** The worker rejects silent recordings, then sends the audio to Scribe v2 with speaker separation.
7. **Process.** One Claude Sonnet 5 call reads the transcript and the chat. It returns the summary, key points, decisions, action items (owner, due date, cited transcript segments) and a name for each speaker label where that is clear. A task typed only in the chat is an action item too. Transcript windows, the summary and each action item are embedded and stored.

## Quick start (local)

Needs Docker, [uv](https://docs.astral.sh/uv/) and Node 22.

```sh
cp .env.example .env            # add the provider keys and BOT_ACCOUNT_LABEL
docker compose up -d --build    # Postgres, Redis, API (runs migrations), workers
```

Sign the bot account in once (see [Bot account](#bot-account)), then open <http://localhost:8040> (API key `dev-api-key` unless you changed `API_KEY`), click **Invite agent**, paste a Meet or Zoom link, and admit the bot when it knocks.

One image, `meeting-agent`, runs the API, both workers and the bots: the worker starts each bot from the same image, as the `bot` user, with the `bot-entrypoint` command.

UI development with hot reload (the API keeps running in compose):

```sh
cd ui && npm install && npm run dev   # http://localhost:5173, proxies /api to :8040
```

Host ports: API and UI `8040`, Postgres `5442`, Redis `6392`.

## Local mock stack

`docker-compose.mock.yml` adds `meeting-agent-mock-anthropic` (`mocks/anthropic/`), which stands in for Anthropic messages and OpenAI embeddings, so no Anthropic or OpenAI account is needed. The bot still joins real meetings, and speech to text is real Scribe v2 with your `ELEVENLABS_API_KEY`.

```sh
claude setup-token              # once; put the token in .env as CLAUDE_CODE_OAUTH_TOKEN
mkdir -p data
docker compose -f docker-compose.yml -f docker-compose.mock.yml up -d --build
```

The mock is `mock_llm_server.py` from `scripts/framework`, with Claude Code (`claude -p`) as its only backend. For embeddings, `claude -p` lists each text's concepts, and each term is hashed into a 1536-dimension vector, so texts that share concepts end up close. `http://localhost:11445/__mock__/calls` lists what it answered. In mock mode the meeting folders are on this machine, in `./data/meetings/<meeting id>/`.

## Configuration

All settings come from `.env` (see `.env.example`) and are read by `meeting_agent/config.py`.

| Variable | Default | What it does |
|---|---|---|
| `API_KEY` | `dev-api-key` | Sent by the UI and API clients as `X-API-Key` |
| `INTERNAL_TOKEN` | `dev-internal-token` | Bot → API events (`X-Internal-Token`) |
| `ELEVENLABS_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | | Speech to text, summary, embeddings |
| `ELEVENLABS_BASE_URL`, `ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL` | | Empty means the provider's own API; the mock compose file sets them |
| `CLAUDE_CODE_OAUTH_TOKEN` | | Mock stack only |
| `SUMMARY_MODEL`, `EMBEDDING_MODEL` | `claude-sonnet-5`, `text-embedding-3-small` | The embedding column has 1536 dimensions |
| `MAX_CONCURRENT_BOTS` | `2` | One `worker-bots` process per bot that may run at once |
| `BOT_ACCOUNT_LABEL` | | Required: the account folder `bot-login` signs into |
| `DEFAULT_EGRESS_MODE` | `direct` | `direct` or `proxy`; the invite dialog can override it |
| `EGRESS_PROXY_URL` | | For `proxy` mode |
| `BOT_DISPLAY_NAME`, `NOTICE_TEXT` | see `strings.py` | The guest name on Zoom; the chat notice |
| `ADMISSION_TIMEOUT_S`, `ALONE_TIMEOUT_S`, `SILENCE_TIMEOUT_S`, `MAX_MEETING_S` | 600, 30, 600, 10800 | |

Every fixed text the agent says, stores or looks for (notice, failure messages, prompt lines, each platform's page labels and selectors) is in `meeting_agent/strings.py`. The summary's instructions are in `prompts/summary.md`.

## Network egress

Meeting platforms judge bots partly by their IP address. Egress is set per bot run, below the meeting page, so it works the same on every platform.

| Mode | What happens |
|---|---|
| `direct` | The host's own address |
| `proxy` | Chromium uses `EGRESS_PROXY_URL`, an HTTP(S) proxy with optional `user:pass@`. WebRTC's non-proxied UDP is turned off in the browser profile, so media goes over TCP through the proxy instead of leaking the host's address |

Before it joins, every bot checks its egress: the public address seen over HTTPS, and the addresses a STUN server sees from WebRTC. In `proxy` mode, a WebRTC address that differs from the HTTPS address stops the run with `egress_leak`. For a bought proxy, a static "ISP" proxy (a fixed home-ISP address) suits bots better than a rotating residential one: an address change mid-call drops the call.

## Bot account

Google Meet refuses guests whose browser is driven by automation ("You can't join this video call"), so every bot is signed in to a Google account. Google also refuses *sign-in* in an automated browser, so the sign-in happens once, by hand, in the bot's own Chromium running as a normal browser:

1. Set `BOT_ACCOUNT_LABEL` in `.env` (e.g. `notetaker`).
2. Start the login screen and open it:

   ```sh
   docker compose --profile login up -d bot-login
   open http://localhost:6080/vnc.html      # Connect, then sign in to the bot's Google account
   ```

3. Sign in, open meet.google.com to check the account shows, close the browser window, then `docker compose --profile login stop bot-login`.

Each bot starts from a copy of `data/accounts/<label>/profile`, so several can run at once. Use a dedicated account (for example `notetaker@yourfirm.com`, named "Lexapar Notetaker"): Meet shows the account's name, and the profile folder holds a live session of that account. Sign in again when Google ends the session; the bot then fails at the join screen. Without a profile, `run_bot` fails the meeting before it starts a bot.

## Deploying on OCI

Jenkins checks the repo out on the OCI host and runs `deploy/oci.sh` there over SSH. The script writes `.env` from the host's secrets file, starts the stack with `docker-compose.server.yml` (no published ports; Nginx Proxy Manager reaches the app through the `meeting-agent-web` Caddy sidecar on `shared_net`), and waits until the API is healthy. On the server, summaries and search vectors come from Claude Code in the `mock-anthropic` container, logged in with a Claude Code token; speech to text is ElevenLabs.

The Jenkins job's shell step:

```bash
#!/bin/bash
set -euo pipefail
# The workspace is on the OCI host itself (Jenkins' data folder lives there).
HOST_WORKSPACE="/home/opc/jenkins_data/workspace/${JOB_NAME}"
ssh -o StrictHostKeyChecking=no opc@cognifuze.com bash "$HOST_WORKSPACE/deploy/oci.sh" "$HOST_WORKSPACE"
```

### One-time host setup

The script checks these and stops with a message if one is missing.

1. **Secrets** in `~/secrets/meeting-agent.env`, readable only by `opc`:

   ```sh
   mkdir -p ~/secrets && (umask 077 && cat > ~/secrets/meeting-agent.env <<EOF
   API_KEY=$(od -An -N20 -tx1 /dev/urandom | tr -d ' \n')
   INTERNAL_TOKEN=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
   ELEVENLABS_API_KEY=<key>
   CLAUDE_CODE_OAUTH_TOKEN=<token from claude setup-token>
   EOF
   )
   grep ^API_KEY= ~/secrets/meeting-agent.env   # the key the UI asks for
   ```

   `CLAUDE_CODE_OAUTH_TOKEN` is the `sk-ant-oat01-…` token. It only works inside Claude Code, so it must not go in `ANTHROPIC_API_KEY`: Anthropic's API refuses it ("invalid x-api-key"). No Anthropic or OpenAI API key is needed.

2. **Docker Compose 2.29.7.** The host's own Compose (2.21) predates `!reset` and `env_file: {required}`, so the deploy uses its own copy and leaves the system one alone:

   ```sh
   mkdir -p ~/tools && cd ~/tools
   curl -fsSLo docker-compose-v2.29.7 https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-aarch64
   echo "6e9fbd5daa20dca5d7d89145081ae8155d68ef2928b497d9f85b54fe0f9dbb2c  docker-compose-v2.29.7" | sha256sum -c -
   chmod +x docker-compose-v2.29.7
   ```

3. **Network:** the Docker network `shared_net` exists, with Nginx Proxy Manager attached to it. NPM forwards the site to `meeting-agent-web:8000`.

4. **Bot account:** after the first deploy, sign the bot in once ([Bot account](#bot-account)) through an SSH tunnel, because the login screen listens on the host's localhost only:

   ```sh
   ssh -L 6080:localhost:6080 opc@cognifuze.com     # from your laptop
   cd <workspace> && ~/tools/docker-compose-v2.29.7 --profile login up -d bot-login
   open http://localhost:6080/vnc.html              # on your laptop
   ```

## API

Every `/api` call needs `X-API-Key`. Every read takes `namespace` (the organisation) and sees only that organisation's rows.

| Call | What it does |
|---|---|
| `POST /api/meetings` | `{namespace, meeting_url, title, join_at?, egress_mode?, metadata?}` → 201, or 422 for an unsupported link |
| `GET /api/meetings?namespace&status&platform&from&to&metadata&limit&offset` | `{items, total, limit, offset}`, newest first. `metadata` is a JSON object, e.g. `{"matter":"M-1"}` |
| `GET /api/meetings/{id}?namespace` | The meeting, its events, transcript segments, summary and action items |
| `GET /api/meetings/{id}/audio?namespace` | The recording (Ogg Opus) |
| `POST /api/meetings/{id}/stop?namespace` | Cancels a scheduled bot, or makes a running bot leave (the recording is still processed) |
| `GET /api/search?namespace&q&kind&platform&from&to&metadata&limit&offset` | Semantic search over transcript windows, summaries and action items |

OpenAPI docs: <http://localhost:8040/docs>.

## Data model

Every table has `namespace`, `created_at` and `updated_at`.

- `meetings`: link, platform, status (`scheduled → joining → waiting → in_meeting → transcribing → processing → done | failed | cancelled`), egress mode, exit IP, admission, failure reason, attendees, `metadata` (JSONB, GIN-indexed).
- `bot_events`: the timeline the bot and the worker report.
- `transcript_segments`: speaker turns with label, name, start and end.
- `summaries` and `action_items` (with the transcript segments they cite).
- `chunks`: the searchable text with `embedding vector(1536)` (HNSW, cosine).

The chat stays in the meeting folder (`chat.json`); it reaches the summary and the action items, not the search index.

## When a platform changes its page

| Layer | What it does |
|---|---|
| Media connection | An init script wraps the browser's WebRTC connections. Meeting audio arriving means the bot was admitted; all connections down for 20 s means the meeting is over (`media_lost`); no sound for `SILENCE_TIMEOUT_S` means nobody is left (`silence`). These signals are the same on every platform. |
| Pop-ups | When a step fails, the bot closes tips and pop-ups (only buttons such as "Got it", "Dismiss", "Not now") and tries the step once more. |
| Selector file | A JSON file at `data/selectors/<platform>.json` (`google_meet.json`, `zoom.json`) replaces any of the selectors in `strings.py` for the next bot, without a rebuild. Patterns are regular expressions, the rest CSS. |
| Evidence | A run that did not end well keeps `debug/last-page.png` and `last-page.html`. |

A fix after a page change is then usually one line in the selector file:

```json
{"join_button": "^(Ask to join|Join now|Request to join)\\b", "leave_button": "button[aria-label^='Leave']"}
```

## Adding a platform

1. Add the link pattern to `meeting_agent/platforms.py` (`Platform`, `detect_platform`, `join_url`).
2. Add the platform's selectors to `meeting_agent/strings.py`.
3. Write an adapter in `meeting_agent/bot/adapters/` that implements `MeetingAdapter` (`base.py`): join, wait for admission, send chat, read chat, set the microphone, list participants, tell when the meeting ended, leave.
4. Register it in `meeting_agent/bot/adapters/__init__.py`.

## Tests

```sh
docker compose up -d postgres redis
uv run pytest -m "not docker"   # API, worker and pipeline, against the real Postgres and Redis
uv run pytest -m docker         # the bot against tests/fake_meeting (docker compose build first)
uv run ruff check . && uv run ruff format --check .
```

The container test runs the real bot image (Xvfb, PulseAudio, Chromium, ffmpeg) against a local fake meeting page. It checks the event order, that the notice was posted and spoken, that the host's chat message was kept and the bot's own was not, and that the recording is not silent.

## Troubleshooting

Each meeting has a folder in the `meeting-agent-data` volume, `meetings/<id>/`:

- `events.jsonl`: everything the bot did, also shown as the timeline in the UI.
- `debug/last-page.png` and `last-page.html`: what the bot saw when a run did not end well.
- `bot.log`: the container's last 500 log lines.
- `audio.ogg`, `chat.json`, `participants.json`, `speaker_timeline.json`.

```sh
docker run --rm -v meeting-agent-data:/data -v "$PWD:/out" alpine cp -r /data/meetings/<id> /out/
```

Common outcomes:

- `admission timeout`: no one admitted the bot in 10 minutes.
- `egress_leak`: WebRTC found a way out around the proxy. Check the proxy.
- `The recording is silent`: the bot was in the call but heard nothing, for example because the platform did not connect its audio.
- `meeting_ended` with `media_lost` or `silence`: the page gave no sign, but the media connection did.

## Known limits of the POC

- The Zoom adapter has not yet joined a live meeting, and its chat selectors are unchecked. Zoom may refuse the web client, and the host must admit the bot. Participant names are not read on Zoom, only the count.
- Meet chat is read from the panel the chat notice opens. If the notice never reaches the chat, no chat is read. Messages typed in the last seconds before the meeting ends can be missed.
- Speaker names come from Meet's captions, the transcript and the participant list.
- The bot cannot be asked to stop from the meeting chat.
- Storage is the host's disk, not S3. There is no user login, no SSO and no retention or deletion schedule.
- On an Apple Silicon Mac the bot uses Playwright's Chromium: Google Chrome has no Linux arm64 build.
