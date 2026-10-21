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
2. **Join.** `run_bot` starts one bot container. The spoken notice is always `data/notice/disclaimer.mp3`, used as it is; nothing is generated, and without the file the bot gives the chat notice only. The bot checks its egress, opens the meeting in headed Chromium, gives its name, turns its camera and microphone off, and asks to join.
3. **Admission.** The bot waits up to 10 minutes. The outcome (`admitted`, `denied`, `timeout`, `blocked`) is stored on the meeting.
4. **Notice.** It posts a chat message and plays the spoken notice through a virtual microphone. If neither works, it leaves without recording.
5. **Record.** ffmpeg records what the meeting plays, as mono 16 kHz Opus (about 14 MB an hour). The bot stops when the meeting ends, when it has been alone for 30 seconds, after 3 hours, or when someone presses **Stop agent**.
6. **Speech to text.** The worker rejects silent recordings, then sends the audio to Scribe v2 with speaker separation.
7. **Process.** One Claude Sonnet 5 call returns the summary, key points, decisions, action items (owner, due date, cited transcript segments) and a name for each speaker label where the transcript makes it clear. Transcript windows, the summary and each action item are embedded and stored.

When a bot run goes wrong, the bot keeps a screenshot and the HTML of its last page, and the worker keeps the container log (see [Troubleshooting](#troubleshooting)).

## Quick start (local)

Needs Docker, [uv](https://docs.astral.sh/uv/) and Node 22.

```sh
cp .env.example .env            # add ELEVENLABS_API_KEY, ANTHROPIC_API_KEY, OPENAI_API_KEY
docker compose up -d --build    # Postgres, Redis, API (runs migrations), workers
open http://localhost:8040      # API key "dev-api-key" unless you changed API_KEY
```

One image, `meeting-agent`, runs the API, both workers and the bots: the worker starts each bot from the same image, as the `bot` user, with the `bot-entrypoint` command. To run without any provider keys, see [Local mock stack](#local-mock-stack).

Then click **Invite agent**, paste a Meet or Zoom link, and admit the bot when it knocks.

UI development with hot reload (the API keeps running in compose):

```sh
cd ui && npm install && npm run dev   # http://localhost:5173, proxies /api to :8040
```

Host ports: API and UI `8040`, Postgres `5442`, Redis `6392`.

## Local mock stack

Two images run the agent without an Anthropic or OpenAI account: `meeting-agent` and `meeting-agent-mock-anthropic`. The bot still joins real meetings, and speech to text is real ElevenLabs Scribe v2 with your `ELEVENLABS_API_KEY`. The mock uses the key `test`.

```sh
claude setup-token              # once; put the token in .env as CLAUDE_CODE_OAUTH_TOKEN
mkdir -p data
docker compose -f docker-compose.yml -f docker-compose.mock.yml up -d --build
```

`meeting-agent-mock-anthropic` (`mocks/anthropic/`) stands in for Anthropic messages and OpenAI embeddings. It is `mock_llm_server.py` from `scripts/framework`, with Claude Code as its only backend: summaries come from `claude -p`. Claude has no embedding model, so for embeddings `claude -p` lists each text's concepts, synonyms and broader terms, and each term is hashed into a fixed position of a 1536-dimension vector. Texts that share concepts end up close without sharing words, and a text always gets the same vector. It does not start without `CLAUDE_CODE_OAUTH_TOKEN`. `http://localhost:11445/__mock__/calls` lists what it answered.

In mock mode the meeting folders are on this machine, in `./data/meetings/<meeting id>/`.

## Configuration

All settings come from `.env` (see `.env.example`) and are read by `meeting_agent/config.py`.

| Variable | Default | What it does |
|---|---|---|
| `API_KEY` | `dev-api-key` | Sent by the UI and API clients as `X-API-Key` |
| `INTERNAL_TOKEN` | `dev-internal-token` | Bot → API events (`X-Internal-Token`) |
| `ELEVENLABS_API_KEY` | | Speech to text |
| `ANTHROPIC_API_KEY` | | Summary and action items |
| `OPENAI_API_KEY` | | Embeddings for search |
| `ELEVENLABS_BASE_URL`, `ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL` | | Empty means the provider's own API; the mock compose file points Anthropic and OpenAI at the mock |
| `CLAUDE_CODE_OAUTH_TOKEN` | | Mock stack only: Claude Code's login inside the mock Anthropic container |
| `SUMMARY_MODEL` | `claude-sonnet-5` | |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | 1536 dimensions; the column size depends on it |
| `MAX_CONCURRENT_BOTS` | `2` | One `worker-bots` process per bot that may run at once |
| `DEFAULT_EGRESS_MODE` | `direct` | `direct`, `proxy` or `tunnel`; the invite dialog can override it |
| `EGRESS_PROXY_URL` | | For `proxy` mode |
| `TS_AUTHKEY`, `TS_EXIT_NODE` | | For the Tailscale container (`tunnel`, or `proxy` through Tailscale) |
| `BOT_ACCOUNT_LABEL` | | For signed-in Meet bots: the account folder `bot-login` signs into |
| `BOT_DISPLAY_NAME` | `Lexapar Notetaker (recording)` | The guest name in the meeting |
| `NOTICE_TEXT` | see `config.py` | Posted in chat (the spoken notice is `data/notice/disclaimer.mp3`) |
| `ADMISSION_TIMEOUT_S`, `ALONE_TIMEOUT_S`, `SILENCE_TIMEOUT_S`, `MAX_MEETING_S` | 600, 30, 600, 10800 | |

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

Google Meet refuses anonymous guests whose browser is driven by automation: Playwright-driven Chromium, and even Playwright- or Patchright-driven Google Chrome, get "You can't join this video call" within 2 seconds, while the same link in a normal Incognito window offers "Ask to join". Signed-in bots are how vendors get past this. Google also tends to refuse *sign-in* in an automated browser, so the sign-in happens once, by hand, in the bot's own Chromium running as a normal browser:

1. Set `BOT_ACCOUNT_LABEL` in `.env` (e.g. `notetaker`).
2. Start the login screen and open it:

   ```sh
   docker compose --profile login up -d bot-login
   open http://localhost:6080/vnc.html      # Connect, then sign in to the bot's Google account
   ```

3. Sign in, open meet.google.com to check the account shows, close the browser window, then `docker compose --profile login stop bot-login`.
4. Invite with identity **Signed-in account**. Each bot starts from a copy of `data/accounts/<label>/profile`, so several can run at once.

Use a dedicated account (for example `notetaker@yourfirm.com`, named "Lexapar Notetaker"): Meet shows the account's name, not `BOT_DISPLAY_NAME`, and the profile folder holds a live session of that account, with access to its mail and Drive. Sign in again when Google ends the session; the bot then fails at the join screen.

## Running on AWS (one EC2 host)

The POC runs the same compose file on one Ubuntu 24.04 EC2 instance. Start with a c7i.xlarge (4 vCPU, 8 GiB) and `MAX_CONCURRENT_BOTS=2`. Published sizing for browser bots is 0.5–4 vCPU and 1–4 GiB each; this bot has not been measured yet.

```sh
git clone <this repo> meeting-agent && cd meeting-agent
./scripts/ec2_setup.sh      # installs Docker, creates .env and stops: fill it in
./scripts/ec2_setup.sh      # builds the images and starts the stack
```

The script adds the `tunnel` profile when `TS_AUTHKEY` is set.

Signing the bot in on EC2 works the same way, through an SSH tunnel, because the login screen is published on the host's localhost only:

```sh
ssh -L 6080:localhost:6080 ubuntu@<host>          # from your laptop
sudo docker compose --profile login up -d bot-login   # on the host
open http://localhost:6080/vnc.html                # on your laptop
```

The profile then lives on that host's EBS volume, next to the bots that use it. Sign in through the same egress the bots will use, so Google sees one location. With several hosts, sign in on each, or keep the profile on shared storage (EFS); Google allows a few dozen concurrent sessions per account. Keep port 8040 closed to the internet: allow only your office address in the security group, or use `ssh -L 8040:localhost:8040 ubuntu@<host>`. Recordings, transcripts and the database stay on the instance's EBS volume.

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

## When a platform changes its page

Meeting pages change without notice, so the bot does not depend on the page alone:

| Layer | What it does |
|---|---|
| Media connection | An init script wraps the browser's WebRTC connections in every frame. Meeting audio arriving means the bot was admitted, even if the page's "Leave" button changed; the admission event says whether the page or the media decided. All connections down for 20 s means the meeting is over (`media_lost`), and no sound for `SILENCE_TIMEOUT_S` (10 minutes) means nobody is left (`silence`). These signals are the same on every platform. |
| Pop-ups | When a step fails, the bot closes tips and pop-ups (buttons such as "Got it", "Dismiss", "Not now", in the page and its frames) and tries the step once more. Only those labels are clicked, never meeting controls. |
| Fallbacks | Each step has a second way in: Meet's join falls back to Enter in the name field, its chat to the Ctrl+Alt+C shortcut. Labels are matched by their start, not exactly. |
| Selector file | Every selector an adapter uses is in its `DEFAULT_SELECTORS`. A JSON file at `data/selectors/<platform>.json` (`google_meet.json`, `zoom.json`) replaces any of them for the next bot, without a rebuild. Patterns are regular expressions, the rest CSS. |
| Evidence | A run that did not end well keeps `debug/last-page.png` and `last-page.html`, which show the new page. |

A fix after a page change is then usually one line in the selector file:

```json
{"join_button": "^(Ask to join|Join now|Request to join)\\b", "leave_button": "button[aria-label^='Leave']"}
```

Still to add: an hourly canary that joins a test meeting and alerts on any failed step, and an AI vision fallback that reads the screenshot when every selector fails.

## Adding a platform

1. Add the link pattern to `meeting_agent/platforms.py` (`Platform`, `detect_platform`, `join_url`).
2. Write an adapter in `meeting_agent/bot/adapters/` that implements `MeetingAdapter` (`base.py`): join, wait for admission, send chat, set the microphone, list participants, tell when the meeting ended, leave. Keep the selectors in one block at the top, with roles and visible text first.
3. Register it in `meeting_agent/bot/adapters/__init__.py`.

Egress, notice, recording, transcription and search need no change. Teams would be the next adapter: its web client as a guest, which Teams holds in the lobby and may label a suspected bot.

## Tests

```sh
docker compose up -d postgres redis
uv run pytest -m "not docker"   # API, worker and pipeline, against the real Postgres and Redis
uv run pytest -m docker         # the bot against tests/fake_meeting (docker compose build first)
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
- `meeting_ended` with `media_lost` or `silence`: the page gave no sign, but the media connection did. Often the meeting simply ended; if it happens while people are still talking, check the platform's page for changes.

## Known limits of the POC

- The Meet and Zoom adapters have not yet joined a live meeting. Their selectors follow the platforms' visible labels and other open-source bots. The fake meeting test covers the bot's machinery, but each platform needs a first real run to confirm its selectors.
- Zoom: it is unresolved whether Zoom's 2026 "on behalf of" token rule covers web-client bots. Zoom may refuse the web client, and the host must admit the bot. Participant names are not read on Zoom, only the count.
- Speaker names come from what the transcript says and the participant list. There is no active-speaker timeline yet.
- The bot cannot be asked to stop from the meeting chat.
- Storage is the host's disk, not S3. There is no user login, no SSO and no retention or deletion schedule.
- On an Apple Silicon Mac the bot uses Playwright's Chromium: Google Chrome has no Linux arm64 build.
