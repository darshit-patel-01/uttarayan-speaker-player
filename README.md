# Uttarayan Song Queue

A self-hosted YouTube-audio jukebox for a shared event. Guests submit YouTube
links via a web form, WhatsApp, or Telegram; songs are validated, queued via
Kafka, and played back-to-back through the host machine's speakers with a
cheerful Hindi TTS announcement between tracks.

## How it works

1. **`producer_api.py`** — FastAPI on port 8000. `POST /enqueue` hands each
   URL to `real_time_validation/` and, if it passes, adds it to both
   `queue_state.json` and Kafka.
2. **`real_time_validation/`** — the single validation path for every request,
   regardless of whether it came from the web UI, the WhatsApp bridge, or the
   Telegram bridge: duplicate-in-queue check, per-requester rate limiting
   (Redis), and content checks (age-restriction, music category, duration).
   See [Validation](#validation) below.
3. **Kafka** — single local broker via Docker Compose. Used only for reliable
   delivery; play order is governed by `queue_state.json`, not Kafka offset.
4. **Redis** — also via Docker Compose. Backs the rate limiter in
   `real_time_validation/`.
5. **`consumer_worker.py`** — long-running player. Reads play order from
   `queue_state.json`, downloads audio with `yt-dlp`, plays via `ffplay`,
   speaks a Hindi TTS announcement between tracks. Commits Kafka messages on
   receipt so songs survive a restart.
6. **`static/index.html`** — single-file web UI: enqueue form, live now-playing
   banner with admin controls, queue manager, play history.

## Prerequisites

- Docker (for the local Kafka broker and Redis)
- Python 3.10+
- `ffmpeg` on your `PATH` (provides `ffplay` for playback)
  - Windows: `winget install ffmpeg`
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
- Audio output on the machine running `consumer_worker.py` — sound plays locally

## Setup

```bash
# 1. Start Kafka + Redis
docker compose up -d

# 2. Create virtualenv and install dependencies
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env — set ADMIN_PASSWORD at minimum
```

## Running

```bash
python run.py
```

Starts Kafka, the API (port 8000), the consumer, and the WhatsApp/Telegram
bridges (if set up — see below) together. `Ctrl+C` stops all of them. Add
`--stop-kafka` to also tear down the Kafka container on exit, or
`--no-whatsapp` / `--no-telegram` to skip a bridge. Each bridge is skipped
automatically if its `node_modules` isn't installed yet, and the Telegram
bridge is also skipped if `TELEGRAM_BOT_TOKEN` isn't set in its `.env`.

**Windows one-command start:** `start.bat` (or `.\start.ps1`) also launches
Docker Desktop if it isn't already running, waits for the daemon to be ready,
then runs `run.py` using `venv\Scripts\python.exe` — no manual venv
activation needed. The venv must already exist (step 2 above).

<details>
<summary>Running pieces separately (useful for debugging)</summary>

```bash
docker compose up -d

# Terminal 1 — player
python consumer_worker.py

# Terminal 2 — API
uvicorn producer_api:app --host 0.0.0.0 --port 8000
```
</details>

## Web UI

Open `http://localhost:8000` in a browser. Tabs:

| Tab | Who | What |
|---|---|---|
| Enqueue Song | Everyone | Paste a YouTube URL to add it to the queue |
| History | Everyone | Last 100 played songs with re-enqueue button |
| Queue | Admin | Live queue with drag-to-reorder and per-song skip |
| Manage Playlist | Admin | Create/edit fallback playlists |

**Admin login** — click the lock icon, enter the username/password from `.env`.
Admin users see playback controls (pause, seek, skip, volume), can reorder the
queue, and their enqueue requests bypass all validation.

### Now-playing banner (admin)

Always visible once a song is playing:

- Live seek bar — click anywhere to jump
- **−10s / +10s** — nudge playback position
- **Pause / Resume**
- **Skip** — jump to next song
- **Volume slider**
- **Up next** — shows the next queued song title

## Adding songs via WhatsApp

`whatsapp-bridge/` is an optional Node.js sidecar. Guests text a YouTube link
to a WhatsApp number and it's forwarded straight to `/enqueue`.

**Note:** uses [Baileys](https://github.com/WhiskeySockets/Baileys) (unofficial
WhatsApp Web protocol — QR-login like linking a device). Not officially
sanctioned by WhatsApp. Use a spare/throwaway number.

### Setup

```bash
cd whatsapp-bridge
npm install
cp .env.example .env   # set ENQUEUE_URL, ADMIN_USERNAME, ADMIN_PASSWORD, ADMIN_PHONE_NUMBERS
npm start
```

Scan the QR code in WhatsApp → Settings → Linked Devices → Link a Device.
Session is saved to `auth_info_baileys/` — no rescan needed unless deleted.

Once `npm install` has been run once, `python run.py` (or `start.bat` on
Windows) starts the bridge automatically alongside everything else — no need
to run `npm start` separately after the first setup.

### How guests use it

- `play https://youtu.be/…` — queues the song; bot replies with position + wait time
- `status` / `queue` / `wait` — returns now-playing info and queue length
- On song start: bot sends the requester a "your song is playing!" notification

### Admin numbers

Numbers in `ADMIN_PHONE_NUMBERS` (digits only, comma-separated, no `+`) bypass
all validation — same as logging in as admin on the web form.

### Request log

`whatsapp-bridge/requests.log` — one line per request and outcome, timestamped.
Gitignored (contains phone numbers). Rotate or delete periodically.

## Adding songs via Telegram

`telegram-bridge/` is an optional Node.js sidecar, same idea as the WhatsApp
bridge but using the official Telegram Bot API (long polling, no public URL
needed).

### Setup

```bash
cd telegram-bridge
npm install
cp .env.example .env   # set TELEGRAM_BOT_TOKEN, ADMIN_USERNAME, ADMIN_PASSWORD, ADMIN_TELEGRAM_IDS
npm start
```

1. Message [@BotFather](https://t.me/BotFather) on Telegram, `/newbot`, copy the
   token it gives you into `TELEGRAM_BOT_TOKEN`.
2. Message [@userinfobot](https://t.me/userinfobot) to get your own numeric
   Telegram user ID for `ADMIN_TELEGRAM_IDS`.

Once `npm install` has been run and `TELEGRAM_BOT_TOKEN` is set,
`python run.py` (or `start.bat` on Windows) starts the bridge automatically
alongside everything else — no need to run `npm start` separately. Pass
`--no-telegram` to skip it.

### How guests use it

Same commands as the WhatsApp bridge:

- `play https://youtu.be/…` — queues the song; bot replies with position + wait time
- `status` / `queue` / `wait` — returns now-playing info and queue length
- On song start: bot sends the requester a "your song is playing!" notification

### Admin IDs

IDs in `ADMIN_TELEGRAM_IDS` (numeric, comma-separated) bypass all validation —
same as logging in as admin on the web form. Telegram doesn't expose phone
numbers to bots, so this uses the sender's numeric user ID instead.

### Request log

`telegram-bridge/requests.log` — one line per request and outcome, timestamped.
Gitignored. Rotate or delete periodically.

## Enqueue API

```bash
curl -X POST http://localhost:8000/enqueue \
  -H "Content-Type: application/json" \
  -d '{"urls": ["https://youtu.be/dQw4w9WgXcQ"]}'
```

Response:
```json
{
  "enqueued": [{
    "id": "aB3x",
    "url": "https://youtu.be/dQw4w9WgXcQ",
    "title": "Song Title",
    "position_in_queue": 2,
    "estimated_wait": "3m 12s"
  }],
  "rejected": [
    {"url": "...", "reason": "Rejected: video is age-restricted"}
  ]
}
```

Admin enqueue (bypasses all validation):
```bash
curl -X POST http://localhost:8000/enqueue \
  -H "Content-Type: application/json" \
  -u admin:yourpassword \
  -d '{"urls": ["https://youtu.be/…"]}'
```

## Other endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/now-playing` | — | Currently playing song + progress |
| `GET` | `/queue` | Admin | Full queue list |
| `GET` | `/wait-time` | — | Queue length + total estimated wait |
| `GET` | `/history` | — | Play history (paginated, searchable) |
| `POST` | `/skip` | Admin | Skip the currently playing song |
| `POST` | `/skip/{song_id}` | Admin | Skip a specific queued song |
| `POST` | `/pause` | Admin | Pause playback |
| `POST` | `/resume` | Admin | Resume playback |
| `POST` | `/seek` | Admin | Seek to position (seconds) |
| `POST` | `/volume` | Admin | Set volume 0–100 |
| `PUT` | `/queue/reorder` | Admin | Reorder queued songs |
| `GET` | `/health` | — | Health check |

## Validation

Every `POST /enqueue` request — from the web UI, the WhatsApp bridge, or the
Telegram bridge — is validated the same way, by `real_time_validation/`, in
this order (cheapest checks first, so a rejected URL never pays for the
network probe below):

1. **Blacklist** (`blacklist.py`) — rejected if the video or the requester is
   on the admin-managed blacklist (see [Blacklist](#blacklist) below). Local
   file, no network. The **video** block applies to everyone including admins;
   the **requester** block is admin-exempt.
2. **Duplicate** (`duplicate.py`) — rejected if the same video is already
   queued and hasn't been skipped.
3. **Rate limit** (`rate_limiter.py`) — rejected if the requester has already
   enqueued `RATE_LIMIT_MAX_SONGS` (default 3) songs in the last
   `RATE_LIMIT_WINDOW_SECONDS` (default 1 hour). Counted in Redis, keyed by
   the requester's phone number (WhatsApp), Telegram user id, or IP address
   (plain web/API requests). Only successfully-validated songs count against
   the limit — a rejected attempt doesn't burn a slot. If Redis is
   unreachable, this check fails **open** (request allowed, not counted) so a
   Redis outage can't silently stop the music.
4. **Content** (`content.py`) — probes the URL via yt-dlp (no download):
   - **Age-restricted** — rejected if YouTube's `age_limit` is 18+
   - **Not music** — rejected unless the video's category is `Music` or it
     carries `track`/`artist` metadata
   - **Too long** — rejected if duration exceeds 2 hours (configurable via
     `MAX_DURATION_SECONDS` in `.env`)

Admin users bypass all of the above except the duplicate check and the
**video** blacklist (metadata is still probed for queue/wait-time accounting).

The WhatsApp and Telegram bridges identify their sender via an
`X-Requester-Id` header on their `/enqueue` calls (phone number / Telegram
user id respectively) so the rate limit and blacklist apply per-person rather
than per-bridge.

## Blacklist

Admins can permanently block **videos** (by YouTube video ID) and
**requesters** (by phone number, Telegram user id, or web IP) for the rest of
the event. Blacklisted requests are rejected first, before any other check.

- A **blocked video** never plays, for **anyone including admins** — blocking a
  video is a hard content ban. To play it again, remove it from the blacklist
  first.
- A **blocked requester** is refused, but **admins are exempt** so the operator
  can't accidentally lock themselves out.

The blacklist persists to `.blacklist.json` (gitignored — it can contain phone
numbers).

Manage it from the **Blacklist** tab in the web UI (admin), or via the API:

| Method | Path | Description |
|---|---|---|
| `GET` | `/blacklist` | List blocked videos + requesters |
| `POST` | `/blacklist/video` | Block a video — body `{"video_id": "<id or URL>"}` |
| `DELETE` | `/blacklist/video/{video_id}` | Unblock a video |
| `POST` | `/blacklist/requester` | Block a requester — body `{"source": "whatsapp\|telegram\|ip", "value": "<id>"}` |
| `DELETE` | `/blacklist/requester?source=…&value=…` | Unblock a requester |

`POST /blacklist/video` accepts either a bare video ID or a full YouTube URL
(the ID is extracted automatically). Blacklisting only blocks **future**
enqueues; a video already in the queue keeps its place unless an admin skips
it.

## TTS announcements

## TTS announcements

Before each queued song, the consumer speaks:
> _"अगला गाना है… [song title]!"_

Uses Microsoft Edge neural TTS (`edge-tts` package) — free, no API key, needs
internet. Voice: `hi-IN-SwaraNeural` (Hindi female). Fails silently if offline.

To test / tune the announcement speed:
```bash
python test_tts.py
```

### Crossfade

For two consecutive songs from the real queue, the announcement above isn't
spoken after the first song ends — it's spoken `CROSSFADE_LEAD_SECONDS`
(default 8) before it ends, overlapping the outgoing song's tail instead of
playing into silence first. The next song then starts the instant the first
one finishes, with no gap. Falls back to the old "announce, then play"
behavior for the first song after startup/idle and for the default-playlist
fallback (which isn't crossfaded).

## Live updates (WebSocket)

The web UI connects to `ws://<host>/ws/now-playing` (or `wss://` over HTTPS)
instead of polling `GET /now-playing`. The backend recomputes the
now-playing/up-next payload once a second and pushes it to every connected
client, so song changes, skips, pauses, seeks, and queue changes all show up
within about a second, for everyone, without per-client HTTP polling. Falls
back to a plain `GET /now-playing` fetch on load and auto-reconnects
(exponential backoff, capped at 15s) if the connection drops.

## Volume normalization

Every song is played through ffplay's `loudnorm` filter (EBU R128, single-pass)
so tracks recorded at very different volumes land at a consistent perceived
loudness, instead of some songs being much louder/quieter than others.
Single-pass trades a few seconds of ramp-up accuracy per song for zero added
startup delay — this app already downloads-then-plays with a prefetch cache
specifically to avoid per-song delays, so a more accurate two-pass analysis
would undo that. Disable with `NORMALIZE_VOLUME=false` in `.env`, or retarget
the loudness with `LOUDNORM_TARGET_LUFS` (default `-16`, standard for
streaming).

## YouTube 403 errors

If songs fail with `HTTP Error 403: Forbidden`:

1. **Update yt-dlp** (most common fix): `pip install -U yt-dlp`
2. **Export cookies**: install "Get cookies.txt LOCALLY" in Chrome → open
   `youtube.com` (logged in) → export → save as `cookies.txt` in the project
   root. yt-dlp picks it up automatically. Re-export when cookies expire
   (weeks–months).

The consumer uses the Android player client by default, which avoids most 403s.
Run `python test_download.py` to diagnose which client works on your machine.

## Queue ordering and restart safety

`queue_state.json` is the authoritative source for both play order and song
metadata (URL, title, duration). Kafka delivers new songs reliably; once a
message arrives, it is committed immediately and `queue_state.json` takes over.

On restart:
- Any song stuck as `"playing"` (from a crash) is reset to `"queued"` and
  replayed from the start.
- Songs in the queue that haven't played yet are picked up from `queue_state.json`
  in order — no songs are lost even if Kafka has no uncommitted messages.

## Fallback / default playlists

When the real queue is empty, the consumer plays songs from whichever admin
playlist is currently **active** (round-robin, looping). A real enqueue
interrupts it immediately.

Manage playlists via the **Manage Playlist** tab (admin). You can create
multiple playlists and switch the active one at any time.

## Public internet access

See [TAILSCALE.md](TAILSCALE.md) — exposes the app over HTTPS via Tailscale
Funnel with no router config or TLS setup required.

Current public URL (while Funnel is active): `https://darshitwindos.tailb36c4a.ts.net`

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `ADMIN_USERNAME` | `admin` | Admin login username |
| `ADMIN_PASSWORD` | *(required)* | Admin login password — must be set |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka broker address |
| `KAFKA_TOPIC` | `youtube_queue` | Kafka topic name |
| `KAFKA_GROUP_ID` | `ytplayer` | Consumer group ID |
| `KAFKA_MAX_POLL_INTERVAL_MS` | `21600000` (6 h) | Max poll interval — must exceed your longest song |
| `MAX_DURATION_SECONDS` | `7200` (2 h) | Songs longer than this are rejected |
| `REDIS_HOST` | `127.0.0.1` | Redis host (rate limiter) |
| `REDIS_PORT` | `6380` | Redis port — not 6379, to avoid colliding with another local Redis-compatible server |
| `REDIS_DB` | `0` | Redis logical DB index |
| `RATE_LIMIT_MAX_SONGS` | `3` | Max songs per requester per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `3600` (1 h) | Rate limit window length |
| `NORMALIZE_VOLUME` | `true` | Apply ffplay's `loudnorm` filter to every song |
| `LOUDNORM_TARGET_LUFS` | `-16` | Target loudness (LUFS) for normalization |
| `CROSSFADE_LEAD_SECONDS` | `8` | How early into a song's tail the next announcement starts |
| `QUEUE_STATE_FILE` | `.queue_state.json` | Shared queue state file path |
| `HISTORY_FILE` | `.history.json` | Play history file path |

## Notes

- **Single consumer / single partition** — strict in-order playback requires
  exactly one `consumer_worker.py` instance. Two instances would play over each other.
- **confluent-kafka** — used instead of `kafka-python` (unmaintained, protocol
  issues with modern brokers). Ships prebuilt wheels; no native install needed.
- **Download-then-play** — each song is fully downloaded to a temp directory
  before playback starts. This adds a brief startup delay per song but avoids
  mid-song CDN drops that would cut playback short with no recovery path.
  The temp file is deleted once playback ends.
- **`KAFKA_MAX_POLL_INTERVAL_MS`** — the consumer blocks for an entire song
  between Kafka polls. Set to 6 hours by default; override if you queue anything longer.
