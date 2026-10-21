# Meeting agent — cost to run end to end

All prices are public list prices in USD, checked on 2026-10-21. Taxes (for example 18% GST on an
India-billed account) are not included. Sources are at the end. Rows marked **estimate** are not
measured yet; section 7 says how to measure them.

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
| Data in from Meet or Zoom | free on AWS | $0 | Only a NAT gateway or a proxy charges for it (section 4). |
| **AI and storage subtotal** | | **≈ $0.28** | Speech to text is about 80% of it. |

Bot compute comes on top. It depends on how the bots run (section 3):

| Bot resources (**estimate**) | vCPU / RAM per bot | Why |
|---|---|---|
| As built: 1280×720 Chromium that receives every participant's video | ≈ 1.5–2 vCPU / 3–4 GB | Software video decoding in Xvfb. This matches `MAX_CONCURRENT_BOTS=2` on a 4-vCPU host. |
| With "receive audio only" (lever 2 in section 6) | ≈ 0.5–1 vCPU / 1.5–2 GB | No video decoding. |

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
| A | **One always-on host** (the current plan) | The same docker compose on one c7i.xlarge. Bots start through the Docker socket. | First live tests | You pay 730 hours a month for any volume. It takes 2 concurrent bots; more need a larger host (c7i.2xlarge $261, c7i.4xlarge $521 per month). |
| B | **One host, office hours only** | The same as A, started and stopped on a schedule (for example 11 h × 22 days = 242 h) | A pilot in one time zone | A meeting outside the window fails. Disk is billed while stopped. |
| C | **Small control host + one Fargate task per bot** | A t4g.medium runs the API, UI, Postgres, Redis and the pipeline. Each meeting starts an ECS `RunTask`, billed per second. | Spiky or growing use, from a pilot upward | It needs the ECS launcher, which the POC does not have, and the bot must upload `audio.ogg` to S3 (there is no shared volume). Fargate cannot run `tunnel` mode, because it gives no `NET_ADMIN` or `/dev/net/tun`. Use `proxy` mode there. The image pull adds about 1–2 min, so start the task before `join_at`. |
| D | **Control host + an EC2 Auto Scaling group of bot hosts** | c7g.2xlarge workers keep the Docker launcher, each with 3 bots (video) or 6 (audio only). The group scales on the bot queue. | Steady volume, or when `tunnel` mode is needed | Idle capacity between scale steps. Scale-in must wait for running bots (scale-in protection). |

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

Compute per bot-hour (video / audio only):

| Where the bot runs | us-east-1 | ap-south-1 |
|---|---|---|
| Fargate x86, 2 vCPU + 4 GB / 1 vCPU + 2 GB | $0.099 / $0.049 | $0.104 / $0.052 |
| Fargate ARM, 2 vCPU + 4 GB / 1 vCPU + 2 GB | $0.079 / $0.040 | $0.058 / $0.029 |
| c7g.2xlarge slot, full (3 bots / 6 bots) | $0.097 / $0.048 | $0.065 / $0.033 |
| c7i.xlarge slot, full (2 bots) | $0.089 | $0.089 |

Each Fargate task with a public IP adds $0.005 per hour for the IPv4 address.

## 4. Network egress (only when AWS IPs are refused)

The bot reaches the meeting from an AWS IP (`direct`), through a proxy (`proxy`), or through the
office line (`tunnel`). Bandwidth per bot is from Google and Zoom: Meet averages 1.3 Mbit/s and
goes up to 4 Mbit/s in a group meeting (0.6–1.8 GB per hour). Audio only is 18–100 kbit/s (0.01–0.05
GB per hour). Zoom is similar: 0.6–1.8 Mbit/s for a group, 60–80 kbit/s for audio.

| Egress | Cost | Per bot-hour (video / audio only) |
|---|---|---|
| `direct` from AWS | $0 | $0 / $0 |
| `tunnel` through the office (Tailscale exit node) | Tailscale $8 per user per month for business use | $0 in cash. It uses 1.3–4 Mbit/s of office upload per bot (about 0.1 Mbit/s with audio only). |
| ISP (static residential) proxy, priced per IP | $1.20–3.50 per IP per month (Oxylabs, Bright Data, Decodo). Bright Data dedicated includes 100 GB per IP per month. | ≈ $0 per hour. Budget one IP for each concurrent bot. |
| Residential proxy, priced per GB | $2.50–8 per GB | **$1.50–14.40** / $0.03–0.40 |

A bot that receives video must not use a per-GB proxy. That one line can cost more than all the
rest of the pipeline.

## 5. Monthly totals by volume

Each total = AI and storage (section 1, with the ElevenLabs minimum) + compute (sections 2 and 3).
Bots receive video, the egress is `direct`, and Mumbai prices are used where the regions differ.
Peak concurrency is assumed at about 1% of monthly meeting-hours (all meetings in office hours).

| Meeting-hours a month | 50 | 200 | 1,000 | 4,000 |
|---|---|---|---|---|
| AI and storage (ElevenLabs + Claude) | $25 | $56 | $280 | $1,120 |
| **A** one always-on c7i.xlarge (c7i.4xlarge at 1,000 h) | $138 → **$163 ($3.26/h)** | $138 → **$194 ($0.97/h)** | $529 → **$809 ($0.81/h)** | not one host |
| **A** with Graviton (c7g.xlarge) | $80 → **$105 ($2.10/h)** | $80 → **$136 ($0.68/h)** | — | — |
| **B** c7i.xlarge, office hours only | $48 → **$73 ($1.46/h)** | $48 → **$104 ($0.52/h)** | — | — |
| **C** t4g.medium + Fargate ARM bots | $26 → **$51 ($1.02/h)** | $37 → **$93 ($0.46/h)** | $92 → **$372 ($0.37/h)** | ≈ $320 → **≈ $1,440 ($0.36/h)** |
| **C** in us-east-1 on Fargate x86 | $36 → **$61 ($1.23/h)** | $53 → **$109 ($0.55/h)** | $145 → **$425 ($0.42/h)** | $487 → **$1,607 ($0.40/h)** |
| Buy: Recall.ai ($0.50 bot + $0.15 transcript) + Claude + control host | **$58 ($1.17/h)** | **$165 ($0.82/h)** | **$733 ($0.73/h)** | **$2,863 ($0.72/h)** |

How to read it:

- Below about 100 hours a month, the always-on host is most of the bill. Buying (Recall.ai, Meeting
  BaaS, Skribby) or strategy C costs less.
- From about 200 hours, strategy C costs about half as much per hour as buying. Strategy A
  costs more than buying until a larger host is busy most of the day.
- At scale, the floor is about $0.30–0.36 per meeting-hour: speech to text ($0.22), Claude ($0.06)
  and bot compute ($0.03–0.06).
- Our cost does not include the engineering time to keep the Meet and Zoom selectors working, or
  to get bots admitted. A vendor's price includes both.

Other hosted bots: Meeting BaaS $0.35–0.50 per hour, plus $0.09–0.125 for transcription. Skribby
$0.35 per hour for the bot, or $0.39–1.36 per hour with transcription. Attendee hosted $0.35–0.50
per hour (it is also open source, so it can be self-hosted for free). Nylas Notetaker $0.65–0.80
per hour. The Recall.ai startup program is $0.25 per hour for the first 10,000 hours.

## 6. Levers, largest first

1. **Do not pay for an idle host.** Below about 200 hours a month, use strategy B or C, or buy.
2. **Receive audio only.** Meet: Settings → Video → Receive resolution → Audio only. Zoom: Stop
   incoming video. This should halve bot CPU or better, so more bots fit on each host. It cuts the
   bandwidth through a proxy, NAT or office line by 20 to 100 times. It is not built yet. First
   check that `participant_names()` still finds the names without video tiles.
3. **Graviton in Mumbai**: about 45% less for the same vCPUs. The image must be built for arm64.
4. **Never use a per-GB proxy for a bot that receives video.** If AWS IPs are refused, use the
   office tunnel or ISP proxies priced per IP.
5. **ElevenLabs**: choose the smallest plan with usage billing (Creator). Every tier has the same
   $0.22 per hour, so a larger plan only adds an unused minimum. Ask for Enterprise pricing above
   about 4,000 hours a month.
6. **Savings Plans** for the always-on part, once the volume is steady: −24% to −34% for a year.
7. **Claude**: set `effort` to `low` or `medium` (thinking tokens bill as output), or use the Batch
   API (−50%). Together these save at most about $0.04 per hour, so they come last.

The silence check already skips Scribe for silent audio, so a failed or empty meeting costs no
speech to text.

## 7. Measure before you decide

These numbers are estimates until a real one-hour meeting runs:

- **Bot CPU, RAM and network**: during a real Meet call, log
  `docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}} {{.NetIO}}'` every 30 s
  for the bot container. Do it once with video and once with audio only. This gives the bots per
  host and the GB per hour.
- **Claude tokens**: store `response.usage` (input and output tokens) on each summary.
- **Scribe**: compare the billed seconds on the ElevenLabs dashboard with `ended_at - started_at`.
- **Bot overhead**: the time from `joining` to `in_meeting`, and from the end of the meeting to
  the bot's exit. This checks the 1.1 factor.

The local test meetings (2026-10-21, 35 s to 3 min) gave only the audio bitrate and the chunk
counts.

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
