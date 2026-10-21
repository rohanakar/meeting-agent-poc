# meeting-agent

A proof of concept: an agent that joins an online meeting, says that it records, records the audio, turns it into a transcript and writes a summary with action items. Transcripts, summaries and action items go into Postgres with pgvector, so they can be searched across meetings, one organisation at a time.

```
Invite → bot joins in a browser → notice in chat and out loud → record → speech to text → store → summary and action items → search
```

It works on **Google Meet** and the **Zoom web client**. The bot is platform-agnostic: each platform is one adapter file, and network egress is set below the meeting page, so every platform shares it.

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
                     audio.ogg, participants.json on the shared volume ◄─┘
                │
                └─► RQ queue "pipeline" ──► transcribe (ElevenLabs Scribe v2)
                                         └► process (Claude summary + OpenAI embeddings → pgvector)
```

1. **Invite.** Paste a meeting link in the UI (or call the API), with a title, the organisation and "now" or a start time. The platform comes from the link.
2. **Join.** `run_bot` makes the spoken notice once (ElevenLabs text to speech) and starts one bot container. The bot checks its egress, opens the meeting in headed Chromium, gives its name, turns its camera and microphone off, and asks to join.
3. **Admission.** The bot waits up to 10 minutes. The outcome (`admitted`, `denied`, `timeout`, `blocked`) is stored on the meeting.
4. **Notice.** It posts a chat message and plays the spoken notice through a virtual microphone. If neither works, it leaves without recording.
5. **Record.** ffmpeg records what the meeting plays, as mono 16 kHz Opus (about 14 MB an hour). The bot stops when the meeting ends, when it has been alone for 2 minutes, after 3 hours, or when someone presses **Stop agent**.
6. **Speech to text.** The worker rejects silent recordings, then sends the audio to Scribe v2 with speaker separation.
7. **Process.** One Claude Sonnet 5 call returns the summary, key points, decisions, action items (owner, due date, cited transcript segments) and a name for each speaker label where the transcript makes it clear. Transcript windows, the summary and each action item are embedded and stored.

When a bot run goes wrong, the bot keeps a screenshot and the HTML of its last page, and the worker keeps the container log (see [Troubleshooting](#troubleshooting)).

## Quick start (local)

Needs Docker, [uv](https://docs.astral.sh/uv/) and Node 22.

```sh
cp .env.example .env            # add ELEVENLABS_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY
docker build -f docker/bot.Dockerfile -t meeting-agent-bot:latest .
docker compose up -d --build    # Postgres, Redis, API (runs migrations), workers
open http://localhost:8040      # API key "dev-api-key" unless you changed API_KEY
```

Then click **Invite agent**, paste a Meet or Zoom link, and admit the bot when it knocks.

UI development with hot reload (the API keeps running in compose):

```sh
cd ui && npm install && npm run dev   # http://localhost:5173, proxies /api to :8040
```

Host ports: API and UI `8040`, Postgres `5442`, Redis `6392`.

## Configuration

All settings come from `.env` (see `.env.example`) and are read by `meeting_agent/config.py`.

| Variable | Default | What it does |
|---|---|---|
| `API_KEY` | `dev-api-key` | Sent by the UI and API clients as `X-API-Key` |
| `INTERNAL_TOKEN` | `dev-internal-token` | Bot → API events (`X-Internal-Token`) |
| `ELEVENLABS_API_KEY` | | Speech to text, and the spoken notice. Without it the bot gives the chat notice only |
| `ANTHROPIC_API_KEY` | | Summary and action items |
| `OPENAI_API_KEY` | | Embeddings for search |
| `SUMMARY_MODEL` | `claude-sonnet-5` | |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | 1536 dimensions; the column size depends on it |
| `MAX_CONCURRENT_BOTS` | `2` | One `worker-bots` process per bot that may run at once |
| `DEFAULT_EGRESS_MODE` | `direct` | `direct`, `proxy` or `tunnel`; the invite dialog can override it |
| `EGRESS_PROXY_URL` | | For `proxy` mode |
| `TS_AUTHKEY`, `TS_EXIT_NODE` | | For the Tailscale container (`tunnel`, or `proxy` through Tailscale) |
| `BOT_ACCOUNT_LABEL`, `FERNET_KEY` | | For signed-in Meet bots |
| `BOT_DISPLAY_NAME` | `Lexapar Notetaker (recording)` | The guest name in the meeting |
| `NOTICE_TEXT` | see `config.py` | Posted in chat and spoken |
| `ADMISSION_TIMEOUT_S`, `ALONE_TIMEOUT_S`, `MAX_MEETING_S` | 600, 120, 10800 | |

## Network egress

Meeting platforms judge bots partly by their IP address, and AWS addresses look like data-centre clients. Egress is set per bot run, below the meeting page, so it works the same on every platform.

| Mode | What happens |
|---|---|
| `direct` | The host's own address |
| `proxy` | Chromium uses `EGRESS_PROXY_URL`, an HTTP(S) proxy with optional `user:pass@` (SOCKS5 is untested, and Chromium cannot send a SOCKS password). WebRTC's non-proxied UDP is turned off in the browser profile, so media goes over TCP through the proxy instead of leaking the host's address |
| `tunnel` | The bot container joins the Tailscale container's network, so all traffic, UDP included, leaves through an exit node on another network |

Before it joins, every bot checks its egress: the public address seen over HTTPS, and the addresses a STUN server sees from WebRTC. In `proxy` and `tunnel` mode, a WebRTC address that differs from the HTTPS address stops the run with `egress_leak`. The meetings list shows egress mode, identity, exit IP and admission for each run, so you can compare how often each setup is admitted.

What routing can and cannot do: it hides the AWS address. It does not get an anonymous guest past Google Meet's "potential risk" queue, which vendors report denies data-centre and spent residential addresses alike. A signed-in bot account is the stronger lever there (see below).

### Office exit node with Tailscale

1. Install Tailscale on a machine on the office line (a Mac mini works) and turn on **Run as exit node**. Approve it in the Tailscale admin console.
2. Create an auth key (reusable, tagged) and set `TS_AUTHKEY=...` and `TS_EXIT_NODE=<the exit node's 100.x address>` in `.env`.
3. Start compose with the profile: `docker compose --profile tunnel up -d`.
4. Invite with egress **Tunnel**. The meeting's exit IP should be the office address.

The same container also serves `proxy` mode through Tailscale: set `EGRESS_PROXY_URL=http://meeting-agent-tailscale:1055`. Use it where the bot cannot run in kernel mode.

Limits: all bots share the office address, and bot traffic uses the office upload bandwidth (roughly 1–3 Mbit/s per bot when video arrives). If the line goes down, the bots go down.

### A bought proxy instead

Static "ISP" proxies (a fixed address registered to a home ISP, billed per IP) fit bots better than rotating residential ones: an address change mid-call drops the call, and per-GB billing makes video expensive. Check the provider's acceptable-use policy before buying. Some providers block Google domains on residential networks, and most now require KYC. Only the URL changes: `EGRESS_PROXY_URL=http://user:pass@host:port`.

## Signed-in Meet bot

Google Meet admits a signed-in account more readily than an anonymous guest.

1. Create a Google account for the bot, ideally in a dedicated Workspace, named the way it should appear in meetings (e.g. "Lexapar Notetaker").
2. Set `FERNET_KEY` in `.env` (the script prints a fresh key if it is missing).
3. On the machine behind the exit node, so Google sees the same address the bots use, run:

   ```sh
   uv sync --extra bot && uv run playwright install chromium
   uv run python scripts/bot_login.py --label notetaker1
   ```

   Sign in in the window that opens. The script saves `data/accounts/notetaker1.enc`.
4. Copy it into the data volume, and set `BOT_ACCOUNT_LABEL=notetaker1`:

   ```sh
   docker run --rm -v meeting-agent-data:/data -v "$PWD/data/accounts:/in" alpine \
     sh -c 'mkdir -p /data/accounts && cp /in/*.enc /data/accounts/'
   ```

5. Invite with identity **Signed-in account**.

## Running on AWS (one EC2 host)

The POC runs the same compose file on one Ubuntu 24.04 EC2 instance. Start with a c7i.xlarge (4 vCPU, 8 GiB) and `MAX_CONCURRENT_BOTS=2`. Published sizing for browser bots is 0.5–4 vCPU and 1–4 GiB each; this bot has not been measured yet.

```sh
git clone <this repo> meeting-agent && cd meeting-agent
./scripts/ec2_setup.sh      # installs Docker, creates .env and stops: fill it in
./scripts/ec2_setup.sh      # builds the images and starts the stack
```

The script adds the `tunnel` profile when `TS_AUTHKEY` is set. Keep port 8040 closed to the internet: allow only your office address in the security group, or use `ssh -L 8040:localhost:8040 ubuntu@<host>`. Recordings, transcripts and the database stay on the instance's EBS volume.

## API

Every `/api` call needs `X-API-Key`. Every read takes `namespace` (the organisation) and sees only that organisation's rows.

| Call | What it does |
|---|---|
| `POST /api/meetings` | `{namespace, meeting_url, title, join_at?, egress_mode?, identity?, metadata?}` → 201, or 422 for an unsupported link |
| `GET /api/meetings?namespace&status&platform&from&to&metadata&limit&offset` | `{items, total, limit, offset}`, newest first. `metadata` is a JSON object, e.g. `{"matter":"M-1"}` |
| `GET /api/meetings/{id}?namespace` | The meeting, its events, transcript segments, summary and action items |
| `GET /api/meetings/{id}/audio?namespace` | The recording (Ogg Opus) |
| `POST /api/meetings/{id}/stop?namespace` | Cancels a scheduled bot, or makes a running bot leave (the recording is still processed) |
| `GET /api/search?namespace&q&kind&platform&from&to&metadata&limit&offset` | Semantic search over transcript windows, summaries and action items |

OpenAPI docs: <http://localhost:8040/docs>.

## Data model

Every table has `namespace`, `created_at` and `updated_at`.

- `meetings`: link, platform, status (`scheduled → joining → waiting → in_meeting → transcribing → processing → done | failed | cancelled`), egress mode, identity, exit IP, admission, failure reason, `metadata` (JSONB, GIN-indexed).
- `bot_events`: the timeline the bot and the worker report.
- `transcript_segments`: speaker turns with label, name, start and end.
- `summaries` and `action_items` (with the transcript segments they cite).
- `chunks`: the searchable text with `embedding vector(1536)` (HNSW, cosine). Search uses iterative index scans, so filters do not starve the results.

## Adding a platform

1. Add the link pattern to `meeting_agent/platforms.py` (`Platform`, `detect_platform`, `join_url`).
2. Write an adapter in `meeting_agent/bot/adapters/` that implements `MeetingAdapter` (`base.py`): join, wait for admission, send chat, set the microphone, list participants, tell when the meeting ended, leave. Keep the selectors in one block at the top, with roles and visible text first.
3. Register it in `meeting_agent/bot/adapters/__init__.py`.

Egress, notice, recording, transcription and search need no change. Teams would be the next adapter: its web client as a guest, which Teams holds in the lobby and may label a suspected bot.

## Tests

```sh
docker compose up -d postgres redis
uv run pytest -m "not docker"   # API, worker and pipeline, against the real Postgres and Redis
uv run pytest -m docker         # the bot image against tests/fake_meeting (build the image first)
uv run ruff check . && uv run ruff format --check .
```

The container test runs the real bot image (Xvfb, PulseAudio, Chromium, ffmpeg) against a local fake meeting page. It checks the event order, that the notice was posted and spoken, and that the recording is not silent. It also checks that a denied bot records nothing.

## Troubleshooting

Each meeting has a folder in the `meeting-agent-data` volume, `meetings/<id>/`:

- `events.jsonl`: everything the bot did, also shown as the timeline in the UI.
- `debug/last-page.png` and `last-page.html`: what the bot saw when a run did not end well. Look here first when a platform changes its page and a selector stops matching.
- `bot.log`: the container's last 500 log lines.
- `audio.ogg`, `participants.json`.

```sh
docker run --rm -v meeting-agent-data:/data -v "$PWD:/out" alpine cp -r /data/meetings/<id> /out/
```

Common outcomes:

- `admission timeout`: no one admitted the bot in 10 minutes, or Meet put it in the risk queue. Try a signed-in account.
- `egress_leak`: WebRTC found a way out around the proxy or tunnel. Check the proxy and the exit node.
- `The recording is silent`: the bot was in the call but heard nothing, for example because the platform did not connect its audio.

## Known limits of the POC

- The Meet and Zoom adapters have not yet joined a live meeting. Their selectors follow the platforms' visible labels and other open-source bots. The fake meeting test covers the bot's machinery, but each platform needs a first real run to confirm its selectors.
- Zoom: it is unresolved whether Zoom's 2026 "on behalf of" token rule covers web-client bots. Zoom may refuse the web client, and the host must admit the bot. Participant names are not read on Zoom, only the count.
- Speaker names come from what the transcript says and the participant list. There is no active-speaker timeline yet.
- The bot cannot be asked to stop from the meeting chat.
- Storage is the host's disk, not S3. There is no user login, no SSO and no retention or deletion schedule.
- On an Apple Silicon Mac the bot uses Playwright's Chromium: Google Chrome has no Linux arm64 build.
