# Meeting agent — cost to run end to end

All prices are public list prices in USD, checked on 2026-10-21. Taxes (for example 18% GST on an
India-billed account) are not included. Sources are at the end. Rows marked **estimate** are not
measured yet; section 8 says how to measure them.

## 1. What one meeting costs

The pipeline is: a bot joins and records → Scribe v2 makes the transcript → one Claude call makes
the summary → embeddings for search. Per hour of meeting:

| Item | Price | Per meeting-hour | Basis |
|---|---|---|---|
| Speech to text, ElevenLabs Scribe v2 (batch, with diarization) | $0.22 per audio-hour | **$0.22** | Same rate on every plan. Diarization is included. Keyterms add $0.05 per hour, but we do not use them. |
| Summary, Claude Sonnet 5 | $2 / $10 per 1M input / output tokens | **≈ $0.06** (estimate) | About 15k input tokens (transcript ≈ 150 words/min, plus a 1.6 KB prompt and the schema). About 3k output tokens, because the call sets no `thinking`, so Sonnet 5 runs adaptive thinking and bills it as output. A second call happens only when the first summary is a placeholder. |
| Search embeddings, OpenAI text-embedding-3-small | $0.02 per 1M tokens | ≈ $0.0003 | Chunks ≈ 1.2 × the transcript, plus the summary. Our test meetings gave 2–6 chunks each. |
| Spoken notice, ElevenLabs TTS Flash | $0.04 per 1K characters | ≈ $0 | One ~150-character clip, made once and cached. |
| Audio storage (Opus, mono 16 kHz) | S3 $0.023 / EBS $0.08 per GB-month | ≈ $0.001 per month kept | Measured: 23–26 kbit/s, so about 10–12 MB per hour. |
| Data in from Meet or Zoom | free on AWS | $0 | Only a NAT gateway or a proxy charges for it (section 5). |
| **AI and storage subtotal** | | **≈ $0.28** | Speech to text is about 80% of it. |

Bot compute comes on top. It is $0.01–0.08 per meeting-hour, depending on the browser layout,
audio only and where the bots run (section 4).

Bot time is longer than meeting time: start-up, the admission wait (up to 10 min) and the 30 s
alone timeout. The tables below use **bot-hours = 1.1 × meeting-hours**.

## 2. Fixed monthly costs

| Item | us-east-1 | ap-south-1 (Mumbai) | Note |
|---|---|---|---|
| c7i.xlarge, always on (the plan's host) | $130.31 | $130.31 | 4 vCPU / 8 GiB, $0.1785 per hour in both regions |
| c7g.xlarge (Graviton), always on | $105.85 | $71.69 | Needs an arm64 bot image. Playwright Chromium has arm64 builds. |
| t4g.medium control host, always on | $24.53 | $16.35 | Derived: one quarter of the t4g.xlarge price |
| EBS gp3, 50 GB | $4.00 | $4.56 | Billed also while the instance is stopped |
| Public IPv4 address | $3.65 | $3.65 | $0.005 per hour |
| ElevenLabs plan | $22 Creator (100 h) · $99 Pro (450 h) · $299 Scale (1,359 h) · $990 Business (4,500 h) | | Plan price = included hours × $0.22, and use above the allowance bills at $0.22 per hour (Creator and up). So the plan is a minimum spend, not a discount. |
| Tailscale (office exit node) | $0 Personal, for non-commercial use only · $8 per user per month Standard | | Exit nodes are included in every plan |

## 3. AWS strategies

| | Strategy | How it runs | Good for | Watch out for |
|---|---|---|---|---|
| A | **One always-on host** (the current plan) | The same docker compose on one c7i.xlarge. Bots start through the Docker socket. | First live tests | You pay 730 hours a month for any volume. It takes 2 concurrent bots with video, or about 4–5 with audio only (section 4). More need a larger host (c7i.2xlarge $261, c7i.4xlarge $521 per month). |
| B | **One host, office hours only** | The same as A, started and stopped on a schedule (for example 11 h × 22 days = 242 h) | A pilot in one time zone | A meeting outside the window fails. Disk is billed while stopped. |
| C | **Small control host + Fargate tasks for the bots** | A t4g.medium runs the API, UI, Postgres, Redis and the pipeline. Each meeting (or each group of meetings, section 4) starts an ECS `RunTask`, billed per second. | Spiky or growing use, from a pilot upward | It needs the ECS launcher, which the POC does not have, and the bot must upload `audio.ogg` to S3 (there is no shared volume). Fargate cannot run `tunnel` mode, because it gives no `NET_ADMIN` or `/dev/net/tun`. Use `proxy` mode there. The image pull adds about 1–2 min, so start the task before `join_at`. |
| D | **Control host + an EC2 Auto Scaling group of bot hosts** | c7g.2xlarge workers keep the Docker launcher. The group scales on the bot queue. | Steady volume, or when `tunnel` mode is needed | Idle capacity between scale steps. Scale-in must wait for running bots (scale-in protection). |

Add-ons for any strategy:

- **Spot**: about 50–66% off (c7i.xlarge $0.060 per hour in us-east-1), with a 2-minute warning.
  Use it for the pipeline worker, which retries its jobs. Do not use it for bots: an interruption
  ends the recording. The interruption band for c7i.xlarge in us-east-1 is above 20%.
- **Savings Plans** for the always-on part: Compute Savings Plan, 1 year, no upfront: −24% to −28%.
  EC2 Instance Savings Plan, 1 year: −34%. A 3-year plan paid up front: −53% to −62%.
- **Graviton and region**: in Mumbai, Graviton costs about 45% less than c7i (c7g.xlarge
  $0.0982 vs $0.1785 per hour). Fargate on ARM there is $0.0238 per vCPU-hour, against $0.0426 on
  x86. In us-east-1 the gap is about 20%.
- **No NAT gateway**: put bots in a public subnet with a public IP. A NAT gateway costs $33–41 a
  month, plus $0.045–0.056 per GB, and a bot that receives video moves 0.6–1.8 GB per hour.
- **Postgres**: keep it on the control host until there is more than one host. RDS db.t4g.medium
  is $47 a month in us-east-1 ($61 in Mumbai), plus storage.

## 4. Browser layout and audio-only mode (measured)

Two choices change the bot's compute and bandwidth:

- **Layout**: one browser per meeting (as built: one container per meeting, each with its own
  Xvfb, PulseAudio, Chromium and ffmpeg), or one browser shared by several meetings, with one
  window for each meeting. The bot drives Chromium with Playwright, not Selenium, but the idea is
  the same. A Selenium Grid would only add a hub process.
- **Audio only**: a per-meeting parameter that tells the platform to send no video (Meet: Settings
  → Video → Receive resolution → Audio only; Zoom: Stop incoming video). It is **not built yet**.

### At a glance

| | Per meeting (M3 Max, measured) | One c7g.2xlarge holds | Compute per meeting-hour (Mumbai) | Monthly total at 4,000 h |
|---|---|---|---|---|
| Video, one browser per meeting (as built) | 0.29 core, 349 MB | 4–7 meetings | $0.03–0.06 | $1,420 |
| Video, one browser for 4 meetings | 0.19 core, 159 MB | 6–10 meetings | $0.02–0.03 | $1,276 |
| Audio only, one browser per meeting | 0.10 core, 305 MB | 11–17 meetings | $0.01–0.03 | $1,293 |
| Audio only, one browser for 4 meetings | 0.06 core, 117 MB | 15–22 meetings | $0.01–0.02 | $1,212 |

- **Audio only** saves the most: about 3× less CPU, and 20 to 100 times less bandwidth. Through a
  proxy priced per GB, a bot costs $1.50–14.40 per hour with video and $0.03–0.40 with audio only.
- **A shared browser** saves only about $0.01–0.03 per meeting-hour, because speech to text
  ($0.22) is still about 70% of the bill. It also needs real work and adds risk (see "What this
  means" below).
- **Recommendation:** build audio only as a meeting parameter, on by default. Keep one browser per
  meeting. Look at a shared browser again above about 2,000 meeting-hours a month.
- **Caveats:** the test did not include Meet's own web app, so the planning numbers add an
  estimate of 0.05 core and 300 MB per meeting for it. The conversion from Mac cores to AWS vCPUs
  is an estimate, which is why the host numbers are ranges. To replace both estimates, run the
  test in the appendix on a real Meet call and on an EC2 host (section 8).

### What was measured

The bot image, its entrypoint (Xvfb + PulseAudio sinks), its Chromium flags and its ffmpeg
recorder were run on 2026-10-21. Each "meeting" was a window that played a 1280×720 VP8 stream at
1.5 Mbit/s (close to Meet's 1.3–1.7 Mbit/s HD inbound) or a 32 kbit/s Opus stream. Every video
window was checked to decode 30 frames per second. CPU is the container's cgroup CPU time over
60 s. Memory is anonymous + shared memory.

| Run | CPU (M3 Max cores) | Memory |
|---|---|---|
| Idle container (blank page, recording) | 0.044 | 291 MB |
| 1 meeting, video | 0.292 | 349 MB |
| 1 meeting, audio only | 0.096 | 305 MB |
| 4 meetings in one browser, video | 0.769 | 634 MB |
| 4 meetings in one browser, audio only | 0.241 | 467 MB |

Per meeting:

| | Video | Audio only |
|---|---|---|
| One browser per meeting | 0.292 core, 349 MB | 0.096 core, 305 MB (**3× less CPU**) |
| One browser, 4 meetings | 0.192 core, 159 MB (**−34% CPU, −55% memory**) | 0.060 core, 117 MB |
| In t3 vCPU, own browser / shared (÷ 0.17, the M3-to-t3 factor measured earlier) | 1.7 / 1.1 | 0.56 / 0.35 |

Limits of the test:

- A real Meet or Zoom page also runs its own web app in every meeting window. That was not part of
  the test. The planning numbers below add **0.05 M3 core and 300 MB per meeting** for it
  (**estimate**).
- A large gallery view can receive up to 4 Mbit/s, which costs more than this test.
- The test ran on an M3 Max. One c7g vCPU is taken as **0.30–0.45 of an M3 Max core**
  (**estimate**), so the per-host numbers are ranges.

### Meetings per host and compute per meeting-hour

Planning per meeting, with the web-app allowance: video 0.342 core / 650 MB in its own browser, or
0.242 / 460 MB when 4 share a browser. Audio only: 0.146 / 605 MB in its own browser, or
0.110 / 420 MB shared. Hosts are filled to 70% CPU.

| Layout | c7g.2xlarge (8 vCPU, 16 GB) holds | $ per meeting-hour on c7g.2xlarge, Mumbai / us-east-1 | Fargate ARM task | $ per meeting-hour on Fargate, Mumbai / us-east-1 |
|---|---|---|---|---|
| Video, one browser per meeting (as built) | 4–7 | $0.028–0.049 / $0.041–0.073 | 2 vCPU / 4 GB per meeting | $0.063 / $0.084 |
| Video, one browser for 4 meetings | 6–10 | $0.020–0.033 / $0.029–0.048 | 4 vCPU / 8 GB for 4 meetings | $0.030 / $0.041 |
| Audio only, one browser per meeting | 11–17 | $0.012–0.018 / $0.017–0.026 | 1 vCPU / 2 GB per meeting | $0.034 / $0.045 |
| Audio only, one browser for 4 meetings | 15–22 | $0.009–0.013 / $0.013–0.019 | 2 vCPU / 4 GB for 4 meetings | $0.016 / $0.021 |

The EC2 prices are for a full host; an Auto Scaling group runs at about 60–75% use, so expect
about 1.5× these. A shared Fargate task costs this much only while all 4 of its slots are in use.
The Fargate prices include $0.005 per hour for each task's public IPv4 address.

### Bandwidth per meeting-hour

| | Video | Audio only |
|---|---|---|
| Data received | 0.6–1.8 GB | 0.01–0.05 GB |
| Through a NAT gateway | $0.03–0.10 | ≈ $0.002 |
| Through a residential proxy priced per GB ($2.50–8) | **$1.50–14.40** | $0.03–0.40 |
| Office upload used in `tunnel` mode | 1.3–4 Mbit/s for each bot | 0.02–0.1 Mbit/s for each bot |

### Monthly compute and total, strategy C (Fargate ARM in Mumbai + a $23 control host)

| Meeting-hours a month | 200 | 1,000 | 4,000 |
|---|---|---|---|
| Video, one browser per meeting | $37 → total $93 ($0.46/h) | $92 → $372 ($0.37/h) | $300 → $1,420 ($0.36/h) |
| Video, one browser for 4 | $29 → $85 ($0.43/h) | $56 → $336 ($0.34/h) | $156 → $1,276 ($0.32/h) |
| Audio only, one browser per meeting | $30 → $86 ($0.43/h) | $60 → $340 ($0.34/h) | $173 → $1,293 ($0.32/h) |
| Audio only, one browser for 4 | $26 → $82 ($0.41/h) | $40 → $320 ($0.32/h) | $92 → $1,212 ($0.30/h) |

Totals include AI and storage ($56, $280 and $1,120 a month).

### What this means

- **Audio only gives most of the compute saving with no change to how the bots run**: about 3×
  less CPU, and about 3 times as many meetings per host. It also cuts bandwidth by 20 to 100 times.
  That matters far more when traffic goes through a proxy, a NAT gateway or the office line. It
  should be the default. Video is only needed when the screen must be recorded.
- **A shared browser saves a further third of the CPU and about half the memory**, but in compute
  that is only about $0.01–0.03 per meeting-hour. At 4,000 hours a month it saves about $80
  (audio only) to $145 (video), against a bill of $1,212–1,420. Speech to text is still about 70%.
- **A shared browser needs real work and adds risk:**
  - **Audio:** one PulseAudio sink mixes every window, so each meeting's audio must be recorded
    inside its page. The bot's `RTCPeerConnection` wrapper in `media.py` can feed the remote
    audio tracks to a `MediaRecorder`.
  - **Spoken notice:** the notice needs a separate microphone for each window. Today one `bot_mic`
    serves the whole container.
  - **Background windows:** the browser needs the three anti-throttling flags used in the test,
    so windows behind others keep running.
  - **Crashes:** one browser crash ends every meeting in it.
  - **Scheduling:** the scheduler must place meetings into browsers, and keep a browser up until
    its last meeting ends.
  - **Egress:** all meetings in the browser leave from one IP (`tunnel` mode). Many bots at once
    from one IP are more likely to be blocked. Playwright can set a proxy for each window, but
    only in `proxy` mode.
- Recommendation: build audio only as a meeting parameter (default on), and keep one browser per
  meeting. Look at a shared browser again above about 2,000 meeting-hours a month, or if a real
  Meet call shows a much larger cost for each browser than this test did.

A sketch of the parameter: `audio_only: bool` on `POST /api/meetings` and in the invite dialog,
with a `BOT_AUDIO_ONLY` default. After admission, the adapter sets the platform's "receive audio
only" setting. Each platform adapter needs one method for it. Check that `participant_names()`
still finds the names on tiles that show no video.

## 5. Network egress (only when AWS IPs are refused)

The bot reaches the meeting from an AWS IP (`direct`), through a proxy (`proxy`), or through the
office line (`tunnel`). Bandwidth per bot is from Google and Zoom: Meet averages 1.3 Mbit/s and
goes up to 4 Mbit/s in a group meeting (0.6–1.8 GB per hour). Audio only is 18–100 kbit/s (0.01–0.05
GB per hour). Zoom is similar: 0.6–1.8 Mbit/s for a group, 60–80 kbit/s for audio.

| Egress | Cost | Per bot-hour (video / audio only) |
|---|---|---|
| `direct` from AWS | $0 | $0 / $0 |
| `tunnel` through the office (Tailscale exit node) | Tailscale $8 per user per month for business use | $0 in cash. It uses office upload bandwidth (section 4). |
| ISP (static residential) proxy, priced per IP | $1.20–3.50 per IP per month (Oxylabs, Bright Data, Decodo). Bright Data dedicated includes 100 GB per IP per month: 55–170 bot-hours of video, or more than 2,000 of audio only. | ≈ $0 per hour. Budget one IP for each concurrent bot. |
| Residential proxy, priced per GB | $2.50–8 per GB | **$1.50–14.40** / $0.03–0.40 |

A bot that receives video must not use a per-GB proxy. That one line can cost more than all the
rest of the pipeline.

## 6. Monthly totals by strategy

Each total = AI and storage (section 1, with the ElevenLabs minimum) + compute. Bots receive video,
one browser per meeting (as built), and egress is `direct`. Mumbai prices are used where the
regions differ. Peak concurrency is assumed at about 1% of monthly meeting-hours (all meetings in
office hours). For audio only or a shared browser, see section 4.

| Meeting-hours a month | 50 | 200 | 1,000 | 4,000 |
|---|---|---|---|---|
| AI and storage (ElevenLabs + Claude) | $25 | $56 | $280 | $1,120 |
| **A** one always-on c7i.xlarge (c7i.4xlarge at 1,000 h) | $138 → **$163 ($3.26/h)** | $138 → **$194 ($0.97/h)** | $529 → **$809 ($0.81/h)** | not one host |
| **A** with Graviton (c7g.xlarge) | $80 → **$105 ($2.10/h)** | $80 → **$136 ($0.68/h)** | — | — |
| **B** c7i.xlarge, office hours only | $48 → **$73 ($1.46/h)** | $48 → **$104 ($0.52/h)** | — | — |
| **C** t4g.medium + Fargate ARM bots | $26 → **$51 ($1.02/h)** | $37 → **$93 ($0.46/h)** | $92 → **$372 ($0.37/h)** | $300 → **$1,420 ($0.36/h)** |
| **C** in us-east-1 on Fargate x86 | $36 → **$61 ($1.23/h)** | $53 → **$109 ($0.55/h)** | $145 → **$425 ($0.42/h)** | $487 → **$1,607 ($0.40/h)** |
| Buy: Recall.ai ($0.50 bot + $0.15 transcript) + Claude + control host | **$58 ($1.17/h)** | **$165 ($0.82/h)** | **$733 ($0.73/h)** | **$2,863 ($0.72/h)** |

How to read it:

- Below about 100 hours a month, the always-on host is most of the bill. Buying (Recall.ai, Meeting
  BaaS, Skribby) or strategy C costs less.
- From about 200 hours, strategy C costs about half as much per hour as buying. Strategy A
  costs more than buying until a larger host is busy most of the day.
- At scale, the floor is about $0.30–0.36 per meeting-hour: speech to text ($0.22), Claude ($0.06)
  and bot compute ($0.02–0.07).
- Our cost does not include the engineering time to keep the Meet and Zoom selectors working, or
  to get bots admitted. A vendor's price includes both.

Other hosted bots: Meeting BaaS $0.35–0.50 per hour, plus $0.09–0.125 for transcription. Skribby
$0.35 per hour for the bot, or $0.39–1.36 per hour with transcription. Attendee hosted $0.35–0.50
per hour (it is also open source, so it can be self-hosted for free). Nylas Notetaker $0.65–0.80
per hour. The Recall.ai startup program is $0.25 per hour for the first 10,000 hours.

## 7. Levers, largest first

1. **Do not pay for an idle host.** Below about 200 hours a month, use strategy B or C, or buy.
2. **Audio only** (section 4): about 3× less bot CPU, and 20 to 100 times less bandwidth. Build
   it as a meeting parameter, default on.
3. **Never use a per-GB proxy for a bot that receives video.** If AWS IPs are refused, use the
   office tunnel or ISP proxies priced per IP.
4. **Graviton in Mumbai**: about 45% less for the same vCPUs. The image must be built for arm64.
5. **ElevenLabs**: choose the smallest plan with usage billing (Creator). Every tier has the same
   $0.22 per hour, so a larger plan only adds an unused minimum. Ask for Enterprise pricing above
   about 4,000 hours a month.
6. **Savings Plans** for the always-on part, once the volume is steady: −24% to −34% for a year.
7. **One browser for several meetings** (section 4): about a third less CPU, but only about
   $0.01–0.03 per meeting-hour, with real engineering work and risk.
8. **Claude**: set `effort` to `low` or `medium` (thinking tokens bill as output), or use the Batch
   API (−50%). Together these save at most about $0.04 per hour.

The silence check already skips Scribe for silent audio, so a failed or empty meeting costs no
speech to text.

## 8. Measure before you decide

- **The real per-bot cost**: during a real Meet call, log
  `docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}} {{.NetIO}}'` every 30 s
  for the bot container. Do it once with video and once with audio only. This replaces the
  web-app allowance and the c7g factor in section 4.
- **The same test on the target instance type**: run the appendix test on a c7g or c7i host. This
  replaces the M3-to-AWS factor.
- **Claude tokens**: store `response.usage` (input and output tokens) on each summary.
- **Scribe**: compare the billed seconds on the ElevenLabs dashboard with `ended_at - started_at`.
- **Bot overhead**: the time from `joining` to `in_meeting`, and from the end of the meeting to
  the bot's exit. This checks the 1.1 factor.

## Appendix: rerun the browser test

The test runs in the bot image (`meeting-agent:latest`), as user `bot`, through the same
`dumb-init` and `bot-entrypoint` as a real bot. It needs no meeting and no network.

1. In an empty folder (for example `/tmp/bot-bench`), make the test media:

   ```sh
   docker run --rm -v "$PWD":/bench -w /bench --entrypoint sh meeting-agent:latest -c '
   ffmpeg -nostdin -loglevel error -y -f lavfi -i testsrc2=size=1280x720:rate=30 \
     -f lavfi -i "sine=frequency=300:sample_rate=48000" -t 90 \
     -c:v libvpx -b:v 1500k -deadline realtime -cpu-used 8 -c:a libopus -b:a 32k video.webm &&
   ffmpeg -nostdin -loglevel error -y -f lavfi -i "sine=frequency=300:sample_rate=48000" -t 90 \
     -c:a libopus -b:a 32k audio.webm'
   ```

2. Add the three pages:

   ```sh
   echo '<!doctype html><body style="margin:0;background:#000"><video id="m" src="video.webm" autoplay loop playsinline style="width:1280px;height:720px"></video></body>' > video.html
   echo '<!doctype html><body style="margin:0"><audio id="m" src="audio.webm" autoplay loop></audio></body>' > audio.html
   echo '<!doctype html><body></body>' > blank.html
   ```

3. Save this as `bench.py`:

   ```python
   """One bot-like container: Chromium under Xvfb, audio to PulseAudio, ffmpeg recording the monitor.
   Usage: bench.py <blank|video|audio> <meetings> — each meeting is its own context (window)."""
   import asyncio, json, subprocess, sys, time
   from playwright.async_api import async_playwright

   ARGS = ["--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required",
           "--disable-blink-features=AutomationControlled", "--lang=en-US",
           "--window-size=1280,720", "--disable-dev-shm-usage",
           # Needed once one browser holds several meetings: windows behind others keep running.
           "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
           "--disable-backgrounding-occluded-windows"]

   def cpu_usec() -> int:
       for line in open("/sys/fs/cgroup/cpu.stat"):
           if line.startswith("usage_usec"):
               return int(line.split()[1])
       raise RuntimeError("no cpu.stat")

   def memory() -> dict:
       stat = dict(line.split() for line in open("/sys/fs/cgroup/memory.stat"))
       return {"current_mb": int(open("/sys/fs/cgroup/memory.current").read()) // 2**20,
               "anon_mb": int(stat["anon"]) // 2**20, "shmem_mb": int(stat["shmem"]) // 2**20}

   async def main(mode: str, meetings: int) -> None:
       recorder = subprocess.Popen(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "pulse",
           "-i", "meeting_out.monitor", "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "32k",
           "-application", "voip", "/tmp/out.ogg"])
       async with async_playwright() as playwright:
           browser = await playwright.chromium.launch(headless=False, args=ARGS)
           pages = []
           for _ in range(meetings):
               context = await browser.new_context(viewport={"width": 1280, "height": 720})
               page = await context.new_page()
               await page.goto(f"file:///bench/{mode}.html")
               pages.append(page)
           await asyncio.sleep(20)
           frames_start = [await frames(page, mode) for page in pages]
           cpu_start, wall_start = cpu_usec(), time.monotonic()
           await asyncio.sleep(60)
           cpu_end, wall_end = cpu_usec(), time.monotonic()
           frames_end = [await frames(page, mode) for page in pages]
           fps = [round((b - a) / (wall_end - wall_start), 1) for a, b in zip(frames_start, frames_end)]
           result = {"mode": mode, "meetings": meetings,
                     "cores": round((cpu_end - cpu_start) / 1e6 / (wall_end - wall_start), 3),
                     "decoded_fps_per_meeting": fps, **memory()}
           await browser.close()
       recorder.terminate()
       print("RESULT " + json.dumps(result), flush=True)

   async def frames(page, mode: str) -> int:
       if mode != "video":
           return 0
       return await page.evaluate("document.getElementById('m').getVideoPlaybackQuality().totalVideoFrames")

   asyncio.run(main(sys.argv[1], int(sys.argv[2])))
   ```

4. Run one case at a time (two runs at once take CPU from each other). The arguments are the mode
   (`blank`, `video` or `audio`) and the number of meetings in one browser:

   ```sh
   run() { docker run --rm --user bot --shm-size=1g -v "$PWD":/bench:ro \
     --entrypoint dumb-init meeting-agent:latest --single-child -- \
     bot-entrypoint python /bench/bench.py "$1" "$2" 2>&1 | grep RESULT; }
   run blank 1; run video 1; run audio 1; run video 4; run audio 4
   ```

   Each run prints one line: `cores` is the CPU used over 60 s (1.0 = one full vCPU on that
   host), `anon_mb` + `shmem_mb` is the memory, and `decoded_fps_per_meeting` must be 30 for every
   video meeting. On an EC2 host, `cores` is already in that host's vCPUs, so no conversion
   factor is needed.

## Sources (all fetched 2026-10-21)

- Claude: https://platform.claude.com/docs/en/about-claude/pricing — Sonnet 5 $2 / $10 per MTok,
  Batch $1 / $5, cache read $0.20, no long-context surcharge, `inference_geo: "us"` costs 1.1×.
- ElevenLabs: https://elevenlabs.io/pricing/api, https://elevenlabs.io/pricing,
  https://elevenlabs.io/docs/overview/capabilities/speech-to-text
- OpenAI: https://developers.openai.com/api/docs/pricing
- AWS: EC2 on-demand price files (published 2026-10-15), the Savings Plans calculator files
  (2026-10-13), the Spot price feed and Spot Instance Advisor, and the bulk price lists for Fargate,
  EBS, S3, data transfer, NAT, VPC IPv4, RDS and ElastiCache (2026-09-16 to 2026-10-20).
  Index: https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/index.json
- Bandwidth: https://knowledge.workspace.google.com/admin/meet/prepare-your-network-for-meet-meetings-and-live-streams,
  https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0060748
- Proxies: brightdata.com/pricing, oxylabs.io/pricing, decodo.com/proxies, iproyal.com/pricing
- Tailscale: https://tailscale.com/pricing
- Hosted bots: https://www.recall.ai/pricing, https://www.meetingbaas.com/pricing,
  https://skribby.io/pricing, https://attendee.dev/pricing, https://www.nylas.com/pricing/

Open points: whether the ElevenLabs plan allowance is one pool shared by speech to text and TTS is
not confirmed. Spot prices are a single snapshot. The t4g.medium price is derived from t4g.xlarge.
