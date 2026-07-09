# YouTube Audio Queue

Post one or more YouTube URLs to a REST endpoint, they get pushed onto a Kafka
queue, and a local worker plays them back to back, in audio-only form,
through your machine's speakers, waiting for each video to finish before
starting the next.

## How it works

1. `producer_api.py` — a FastAPI endpoint. `POST /enqueue` takes a JSON list
   of YouTube URLs, validates each one (see Validation below), and publishes
   one Kafka message per URL that passes.
2. Kafka — a single local broker (via Docker Compose, using the official
   `apache/kafka` image) holds the queue.
3. `consumer_worker.py` — a long-running consumer. It reads one message at a
   time, resolves the direct audio stream with `yt-dlp`, and pipes it straight
   into `ffplay` (no file is downloaded to disk). It blocks until playback
   finishes, commits the offset, then moves to the next message — so videos
   always play strictly one after another, in the order they were enqueued.

## Prerequisites

- Docker (for the local Kafka broker)
- Python 3.10+
- `ffmpeg` installed and on your `PATH` (provides `ffplay`, used for playback)
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
  - Windows: `winget install ffmpeg` (or download from ffmpeg.org and add to PATH)
- Working audio output on the machine that runs `consumer_worker.py` — this
  must be run on your own computer, not in a remote/headless environment,
  since that's where the sound will actually play.

## Setup

```bash
# 1. Start Kafka locally
docker compose up -d

# 2. Create a virtualenv and install dependencies
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 3. Copy the env file (defaults already match the docker-compose broker)
cp .env.example .env
```

## Running

**Single command** (starts Kafka, the API, and the player together):

```bash
python run.py
```

This runs `docker compose up -d`, waits for Kafka to accept connections, then
launches `producer_api` (on port 8000) and `consumer_worker.py` as child
processes. Ctrl+C stops both cleanly. Add `--stop-kafka` if you also want the
Kafka container torn down on exit (`python run.py --stop-kafka`).

<details>
<summary>Running the pieces separately instead (useful for debugging)</summary>

```bash
docker compose up -d

# Terminal 1 — the consumer/player
python consumer_worker.py

# Terminal 2 — the API
uvicorn producer_api:app --host 0.0.0.0 --port 8000
```

</details>

## Adding songs via WhatsApp

See [whatsapp-bridge/README.md](whatsapp-bridge/README.md) for an optional
sidecar that lets people enqueue songs by texting a YouTube link to a
WhatsApp number, instead of using the web form.

## Usage

Enqueue one or more videos:

```bash
curl -X POST http://localhost:8000/enqueue \
  -H "Content-Type: application/json" \
  -d '{"urls": [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/aqz-KE-bpKQ"
  ]}'
```

Each valid URL lands in the queue and `consumer_worker.py` plays them in
order, one at a time, waiting for each to finish before starting the next.

The response reports both outcomes. Each accepted song gets an incremental
ID, its position in the queue, and an estimated wait time — the summed
duration of every song currently ahead of it (including the remaining time
left on whatever's currently playing):

```json
{
  "enqueued": [
    {
      "id": 7,
      "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
      "position_in_queue": 3,
      "duration_seconds": 212,
      "duration": "3m 32s",
      "estimated_wait_seconds": 401,
      "estimated_wait": "6m 41s"
    }
  ],
  "rejected": [
    {"url": "https://youtu.be/some-id", "reason": "Rejected: video is age-restricted / adult content"}
  ]
}
```

`position_in_queue` of 1 means it plays next. `estimated_wait` is when it's
expected to *start*, not when it finishes. This is tracked in a small shared
state file (`.queue_state.json`) that the API and the consumer both read and
write — fine for a single local user, not a proper database.

Skip the song currently playing (moves straight to the next one in queue):

```bash
curl -X POST http://localhost:8000/skip
```

If nothing is playing when you call this, it's a no-op.

Health check: `GET http://localhost:8000/health`

## Validation

Before a URL is queued, `validators.py` probes it (via `yt-dlp`, no download)
and rejects it if either check fails:

- **Adult / age-restricted content** — rejected if YouTube's `age_limit`
  flag is 18+. This is the closest public signal YouTube exposes; there is
  no explicit "is this pornographic" API, so this catches videos YouTube
  itself has flagged as mature/age-restricted.
- **Not a song** — rejected unless the video's category is `Music`, or it
  carries `track`/`artist` metadata (set on YouTube Music uploads). Regular
  vlogs, podcasts, tutorials, etc. get rejected here.

This is a heuristic, not a guarantee: some legitimate music videos are
mis-categorized by uploaders and may get rejected, and this won't catch
adult content that YouTube itself hasn't age-flagged. Tighten or loosen the
checks in `validate_song_url()` if you find false positives/negatives.

## Exposing this to the public internet

See [TAILSCALE.md](TAILSCALE.md) for how to make this app reachable from
outside your local network over HTTPS, using Tailscale Funnel — no router
port-forwarding or manual TLS setup required.

## Notes / things you may want to change later

- **Download-then-play**: each song is downloaded with `yt-dlp` to a
  temporary directory, then played from that local file — not streamed
  directly from YouTube's CDN into `ffplay`. An earlier direct-stream
  version was simpler and needed no cleanup, but the CDN connection could
  drop mid-song (`TLS`/`IO error -10054`) and cut playback short with no way
  for `ffplay` to recover. `yt-dlp`'s downloader retries properly, so this
  trades a short startup delay per song for reliable full-length playback.
  The temp file is removed once playback ends, however it ends.
- **`KAFKA_MAX_POLL_INTERVAL_MS`**: the consumer blocks for an entire song
  between calls to Kafka's `poll()`. Kafka's default timeout for that
  (5 minutes) would otherwise evict the consumer mid-song and redeliver the
  same message, causing it to play twice. This is set to 6 hours by default
  — override it in `.env` if you queue anything longer than that.
- **Single consumer / single partition**: strict in-order playback relies on
  running exactly one instance of `consumer_worker.py`. Running two at once
  would let them grab messages in parallel and play over each other.
- **confluent-kafka**: used as the Kafka client (instead of `kafka-python`),
  since `kafka-python`/`kafka-python-ng` are unmaintained and misbehave
  against modern brokers (coordinator discovery loops, protocol mismatches).
  `confluent-kafka` wraps `librdkafka` and ships prebuilt wheels for Windows,
  macOS, and Linux, so no separate native install is needed.
