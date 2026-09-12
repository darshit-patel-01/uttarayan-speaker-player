# Uttarayan Song Queue

A self-hosted YouTube-audio jukebox for a shared event. Guests submit YouTube
links via a web form, WhatsApp, or Telegram; songs are validated, queued in
SQLite, and played back-to-back through the host machine's speakers with a
cheerful Hindi TTS announcement between tracks.

## How it works

1. **`producer_api.py`** — FastAPI on port 8000. `POST /enqueue` hands each
   URL to `real_time_validation/` and, if it passes, inserts it into the
   `queue_items` table in SQLite.
2. **`real_time_validation/`** — the single validation path for every request,
   regardless of whether it came from the web UI, the WhatsApp bridge, or the
   Telegram bridge: duplicate-in-queue check, per-requester rate limiting,
   and content checks (age-restriction, music category, duration).
   See [Validation](#validation) below.
3. **SQLite (`uttarayan.db`)** — the one shared store. Queue, play history,
   playlists, blacklists, settings, rate-limit counters and admin messages all
   live here; WAL mode lets the API and the player read and write it
   concurrently. No external services are needed.
4. **`consumer_worker.py`** — long-running player. Polls `queue_items` for the
   next queued row, downloads audio with `yt-dlp`, plays via `mpv`, speaks a
   TTS announcement between tracks.
5. **`static/index.html`** — single-file web UI: enqueue form, live now-playing
   banner with admin controls, queue manager, play history.

## Prerequisites

- Python 3.10+
- `mpv` on your `PATH` (playback)
  - Windows: `winget install mpv`
  - macOS: `brew install mpv`
  - Ubuntu/Debian: `sudo apt install mpv`
- `ffmpeg` on your `PATH` (audio extraction for `yt-dlp`)
  - Windows: `winget install ffmpeg`
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
- Audio output on the machine running `consumer_worker.py` — sound plays locally

## Setup

**Windows:** double-click `start.bat`. On first run it creates the virtualenv
and installs dependencies itself, then starts the app. You only need Python,
`mpv` and `ffmpeg` installed (see above). Later runs start straight away, and
it re-installs dependencies automatically if `requirements.txt` changes.

**Manual / other platforms:**

```bash
# 1. Create virtualenv and install dependencies
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env — set ADMIN_PASSWORD at minimum
```

## Running

```bash
python run.py
```

Starts the API (port 8000), the player, and the WhatsApp/Telegram bridges
(if set up — see below) together. `Ctrl+C` stops all of them. Add
`--no-whatsapp` / `--no-telegram` to skip a bridge. Each bridge is skipped
automatically if its `node_modules` isn't installed yet, and the Telegram
bridge is also skipped if `TELEGRAM_BOT_TOKEN` isn't set in its `.env`.

If the app is already running, a second `python run.py` (or `start.bat`)
attaches to its log output instead of starting a duplicate; `Ctrl+C` there
stops the running app.

<details>
<summary>Running pieces separately (useful for debugging)</summary>

```bash
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
| Enqueue | Everyone | Search YouTube or paste a URL, add an optional dedication, see the current wait time |
| History | Everyone | Play history (searchable, paginated) with one-click re-enqueue |
| Dashboard | Admin | Songs played / playtime / top requesters, and the 50 most-requested songs (re-enqueueable) |
| Queue | Admin | Live queue with drag-to-reorder, per-song skip, bump-to-front, multi-select skip, clear |
| Playlist | Admin | Create/edit fallback playlists and choose the active one |
| Blacklist | Admin | Block videos or requesters; one-click block from the recent-requesters list |
| Messages | Admin | Appeals from blocked users, with replies delivered back over WhatsApp/Telegram |
| Settings | Admin | Live tuning of limits, TTS, dedications, playlist mode, sharing — see [Admin settings](#admin-settings-live) |

Every song title in History, Dashboard and Playlist has an **Enqueue** button
beside it. When dedications are enabled it first opens a small dialog for the
requester's name and who the song is for; when they're off it enqueues
straight away. Feedback everywhere is a kite-themed toast — a successful
action shouts *Kai Po Che!*, a failure *E Lapet!*, and a kite gets cut in the
background either way.

**Admin login** — click the kite icon top-right and enter the username/password
from `.env`. Admins get the playback controls (pause, seek, skip, volume), the
admin tabs above, and their enqueue requests bypass every validation check
except the video blacklist. The browser session is a token signed by the
running server process, so **restarting the server logs every browser out** —
the bridges are unaffected, they authenticate per request. Sessions otherwise
last 12 hours.

### Now-playing banner

Always visible once a song is playing, including during its TTS announcement:

- Live seek bar — click anywhere to jump (admin)
- **−10s / +10s** — nudge playback position (admin)
- **Pause / Resume**, **Skip** (admin)
- **Volume slider** (admin)
- **Up next** — the next queued song, or the next fallback-playlist track
- Dedication line ("💜 Dedicated by … for …") when the song has one

## Sharing (QR code)

The QR button top-right opens a share modal with three tabs — **WhatsApp**,
**Telegram** and **Web** — each with a scannable code, the link it encodes,
a copy button and scan instructions.

- **WhatsApp / Telegram** point at whatever account the bridge is *actually*
  signed in as. Each bridge reports its identity to the server on every
  connect (`POST /share/bridge-identity`), so relinking WhatsApp to a
  different phone or swapping the BotFather token corrects the QR by itself;
  the modal marks these "✓ Auto-detected from the connected bridge". An admin
  can still type a value by hand while a bridge is offline — the next bridge
  connect overwrites it.
- **Web** encodes the public Tailscale Funnel URL when one is serving the API
  port (see [Public internet access](#public-internet-access)), otherwise the
  address the page was loaded from. The modal says which it is.

## Admin settings (live)

The **Settings** tab overrides the `.env` defaults below without a restart —
the API reads each value per request and the player per song, so a change
takes effect on the next request / next song. **Reset all to defaults** clears
every override.

| Setting | Default | What it does |
|---|---|---|
| Playlist mode | off | Reject all guest requests; only the active playlist plays |
| Songs per user per window | 3 | Rate limit (admins exempt) |
| Rate-limit window | 1 h | Window for the above |
| Max queue wait | 2 h | Reject new songs once the queue's total wait exceeds this (0 = off) |
| Max song length | 2 h | Reject longer songs |
| Duplicate history check | 10 | Reject a song played within the last N songs (0 = off; admins exempt) |
| Stuck song timeout | 120 s | Auto-skip if download + playback hasn't started by then |
| Volume normalization | on | Even out loudness between songs |
| Loudness target | −16 LUFS | Target for normalization |
| Crossfade lead | 8 s | How early the next announcement starts before the current song ends |
| Song dedications | on | Show the dedication fields and read dedications aloud; when off the UI hides them and any sent are dropped |
| Song announcements | on | Speak a TTS intro before each song; off plays songs back-to-back with no voice (dedications aren't read out either) |
| TTS language | Hindi | Hindi or English announcements |
| Share public (Tailscale) link | on | Use the Funnel URL in the Web QR whenever the funnel is up |

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

On every connect the bridge tells the server which phone number it's linked
as, so the share QR's WhatsApp link always tracks the real account — there's
no number to type in anywhere. This is an admin call, so it needs
`ADMIN_USERNAME`/`ADMIN_PASSWORD` in the bridge's `.env`; without them the
bridge still works, it just logs a warning and the QR keeps whatever an admin
entered by hand.

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

On start the bridge asks Telegram for its own `@username` and reports it to
the server, so the share QR's Telegram link follows the token in use — swap
bots and the QR updates on the next start. Needs `ADMIN_USERNAME`/
`ADMIN_PASSWORD` in the bridge's `.env`, same as WhatsApp.

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

Interactive docs at `http://localhost:8000/docs`. "Admin" means either a
browser session token (`Authorization: Bearer …` from `/login`) or HTTP Basic
with the admin credentials — the latter is what the bridges send.

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/login` | Basic | Check credentials, get a 12-hour session token |
| `GET` | `/me` | Admin | Is this session still valid? 401 once the server has restarted |
| `GET` | `/now-playing` | — | Currently playing song, progress, up next |
| `GET` | `/wait-time` | — | Queue length, total estimated wait, whether dedications are on |
| `GET` | `/search?q=` | — | YouTube search for the Enqueue tab |
| `GET` | `/status/{song_id}` | — | Where a previously enqueued song is in the queue |
| `GET` | `/my-songs` | — | The caller's own queued songs (identified by IP / phone / Telegram id) |
| `POST` | `/cancel-last` | — | Cancel the caller's most recent queued song |
| `GET` | `/history` | — | Play history (paginated, searchable) |
| `GET` | `/queue` | Admin | Full queue |
| `PUT` | `/queue/reorder` | Admin | Reorder queued songs |
| `POST` | `/bump/{song_id}` | Admin | Move a song to play next |
| `POST` | `/skip` · `/skip/{song_id}` · `/queue/skip-multiple` · `/queue/clear` | Admin | Skip the current song / one / several / all |
| `POST` | `/pause` · `/resume` · `/stop` · `/seek` | Admin | Playback control |
| `GET` `POST` | `/volume` | Admin | Read / set volume 0–100 |
| `GET` | `/playback-status` | Admin | Paused / stopped flags |
| `GET` | `/stats` | Admin | Dashboard numbers |
| `GET` `PUT` | `/config` · `POST /config/reset` | Admin | Live settings behind the Settings tab |
| `GET` | `/share/config` | — | Share-link config plus the resolved public/local web URL |
| `POST` | `/share/config` | Admin | Set the WhatsApp number / Telegram bot by hand |
| `POST` | `/share/bridge-identity` | Admin | Bridges report the account they're signed in as |
| `GET` | `/share/qr?target=` | — | SVG QR code for `whatsapp`, `telegram` or `web` |
| `POST` | `/announce` | Admin | PA announcement: JSON `{"text"}` (TTS) or raw `audio/*` clip; pauses the song, plays, resumes |
| — | `/playlists…` · `/blacklist…` · `/messages…` | Admin* | Playlists, blacklist and appeals — see `/docs`. *`/messages/appeal` and `/messages/outbox…` are open: they're how blocked users and the bridges reach the admin |
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
   `RATE_LIMIT_WINDOW_SECONDS` (default 1 hour). Counted in the SQLite
   `rate_limits` table, keyed by the requester's phone number (WhatsApp),
   Telegram user id, or IP address (plain web/API requests). Only
   successfully-validated songs count against the limit — a rejected attempt
   doesn't burn a slot. If the database is unreachable, this check fails
   **open** (request allowed, not counted) so a storage hiccup can't silently
   stop the music.
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

Every song is played through mpv's `loudnorm` filter (EBU R128, single-pass)
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

The SQLite `queue_items` table is the authoritative source for both play
order and song metadata (URL, title, duration). The API inserts accepted
songs there and the player polls it (every 200 ms while idle) for the next
queued row, so nothing else has to be running for a request to reach the
speakers.

On restart:
- Any song stuck as `"playing"` (from a crash) is reset to `"queued"` and
  replayed from the start.
- Songs in the queue that haven't played yet are picked up in order — no
  songs are lost across a restart.

## Fallback / default playlists

When the real queue is empty, the consumer plays songs from whichever admin
playlist is currently **active** (round-robin, looping). A real enqueue
interrupts it immediately.

Manage playlists via the **Manage Playlist** tab (admin). You can create
multiple playlists and switch the active one at any time.

## Public internet access

See [TAILSCALE.md](TAILSCALE.md) — exposes the app over HTTPS via Tailscale
Funnel with no router config or TLS setup required.

The server detects the Funnel itself (`tailscale serve status --json`, cached
for 30 s). While a funnel is proxying to the API port, the share modal's
**Web** QR and link switch to the public `https://<device>.<tailnet>.ts.net`
address, and fall back to the local address when the funnel is off. Turn this
off with the **Share public (Tailscale) link** setting if you'd rather always
hand out the LAN address. Hosts served on the tailnet *without* Funnel are
ignored — they're no more reachable to a guest than localhost.

Current public URL (while Funnel is active): `https://darshitwindos.tailb36c4a.ts.net`

## Configuration (`.env`)

Everything in the [Admin settings](#admin-settings-live) table can be
overridden live from the Settings tab; the values here are the defaults it
falls back to.

| Variable | Default | Description |
|---|---|---|
| `ADMIN_USERNAME` | `admin` | Admin login username |
| `ADMIN_PASSWORD` | *(required)* | Admin login password — must be set |
| `API_PORT` | `8000` | Port the API and web UI listen on. Change it if something else already owns 8000 (Splunk's web UI defaults to it, for one) |
| `RATE_LIMIT_MAX_SONGS` | `3` | Max songs per requester per window (admins exempt) |
| `RATE_LIMIT_WINDOW_SECONDS` | `3600` (1 h) | Rate limit window length |
| `MAX_QUEUE_WAIT_SECONDS` | `7200` (2 h) | Reject new songs once the queue's total wait exceeds this; `0` disables |
| `MAX_DURATION_SECONDS` | `7200` (2 h) | Songs longer than this are rejected |
| `DUPLICATE_HISTORY_COUNT` | `10` | Reject a song played within the last N songs; `0` disables |
| `STUCK_TIMEOUT_SECONDS` | `120` | Auto-skip a song whose download + playback hasn't started in time |
| `PLAYLIST_MODE` | `false` | Reject all guest requests; only the active playlist plays |
| `DEDICATIONS_ENABLED` | `true` | Let requesters attach a dedication, announced via TTS |
| `ANNOUNCEMENTS_ENABLED` | `true` | Speak a TTS announcement before each song |
| `TTS_LANGUAGE` | `hi` | `hi` (Hindi) or `en` (English) announcements |
| `NORMALIZE_VOLUME` | `true` | Apply mpv's `loudnorm` filter to every song |
| `LOUDNORM_TARGET_LUFS` | `-16` | Target loudness (LUFS) for normalization |
| `CROSSFADE_LEAD_SECONDS` | `8` | How early into a song's tail the next announcement starts |
| `USE_PUBLIC_URL` | `true` | Put the Tailscale Funnel URL in the Web share QR whenever the funnel is up |
| `DB_FILE` | `uttarayan.db` | Path of the SQLite database everything lives in |
| `ANALYTICS_MAX_EVENTS` | `5000` | Cap on stored analytics events |

The legacy `*_FILE` variables (`QUEUE_STATE_FILE`, `HISTORY_FILE`, …) only
point at old JSON files for the one-time import into SQLite — you can ignore
them.

## Notes

- **Single player** — strict in-order playback requires exactly one
  `consumer_worker.py` instance. Two instances would play over each other.
- **SQLite instead of a message broker** — the API and player only ever share
  state through one local database file. That is the whole hand-off, so
  there is nothing extra to install or keep running; the trade-off is that
  both processes have to be on the same machine, which is how this is used.
- **Download-then-play** — each song is fully downloaded to a temp directory
  before playback starts. This adds a brief startup delay per song but avoids
  mid-song CDN drops that would cut playback short with no recovery path.
  The temp file is deleted once playback ends.
