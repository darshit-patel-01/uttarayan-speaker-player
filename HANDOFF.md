# Handoff notes

Summary of everything done across all working sessions, for picking the project
back up later or handing it to someone else.

---

## Project location

**Primary/active location:** `C:\Users\darsh\Git\uttarayan-speaker-player`

`C:\Users\darsh\PycharmProjects\ytplayer` is the old location — treat it as
stale and safe to delete.

---

## What this project is

A self-hosted YouTube-audio jukebox for a shared event (Uttarayan kite festival):
guests submit YouTube links via a web form or WhatsApp, they are validated and
queued via Kafka, and a local worker plays them back-to-back through the host
machine's speakers. Full setup details are in [README.md](README.md).

---

## Session 1 — initial build (2026-07-08)

### Core app
- FastAPI `/enqueue` endpoint validates and publishes to Kafka
- `consumer_worker.py` reads from Kafka and plays audio via yt-dlp + ffplay
- Song validation: rejects age-restricted content, non-music videos, videos over 2 hours
- Admin bypass: HTTP Basic auth on `/enqueue` skips all validation
- Default/fallback playlist: plays on loop when the real queue is empty, interrupted immediately when a real song is enqueued
- Duration-aware wait time: `/wait-time` returns summed remaining durations of all queued songs
- Web UI (`static/index.html`): enqueue form, live countdown, queue status

### Security
- `config.py` — all secrets via `.env`, no hardcoded defaults
- `ADMIN_PASSWORD` must be set manually in `.env`

### Public internet access
- Tailscale Funnel on port 8000 — documented in [TAILSCALE.md](TAILSCALE.md)
- Public URL: `https://darshitwindos.tailb36c4a.ts.net`
- Funnel survives reboots; `python run.py` does not — must be started manually

### WhatsApp bridge (`whatsapp-bridge/`)
- Node.js sidecar using Baileys (unofficial WhatsApp Web protocol)
- Watches a WhatsApp number for `play <youtube link>` messages, forwards to `/enqueue`
- Pinned to `@whiskeysockets/baileys@7.0.0-rc13` for LID→phone resolution
- `ADMIN_PHONE_NUMBERS` in `whatsapp-bridge/.env` — numbers whose requests bypass validation
- Every request logged to `whatsapp-bridge/requests.log` (gitignored)
- **Not officially sanctioned by WhatsApp** — use a spare number

### Docker
- Removed orphaned `kafka` and `kafka-demo` containers from the old path, reclaimed ~5.2 GB

---

## Session 2 — features, UI polish, and bug fixes (2026-07-09)

### Admin playback controls
- Now-playing banner shows: song title, uploader, seek bar (live progress), Pause/Resume, −10s, +10s, Skip buttons
- Volume slider (admin-only) — adjusts ffplay volume via a `.volume` signal file
- Seek: writes offset to `.seek_signal`; playback restarts from that position
- Pause/Resume: uses `psutil` to `SIGSTOP`/`SIGCONT` the ffplay process (cross-platform)
- **Removed Stop button** — Skip does the same job

### Queue management
- Queue tab added (admin-only): shows all queued and playing songs
- **Drag-and-drop reordering** in both Queue tab and Manage Playlist modal — same UX pattern
- Queue reorder actually affects play order (see architecture note below)
- Per-song Skip button in queue (marks `skip_requested`; consumer discards before play)
- Bump-to-front API (`POST /bump/{song_id}`) — superseded by drag-and-drop but kept in backend

### Architecture: queue ordering (important)
Previously Kafka message order determined play order — reordering `queue_state.json`
only changed the display. Redesigned in this session:

- `queue_state.json` is now the **authoritative play order and data source**
- Consumer commits Kafka messages immediately on receipt (Kafka = delivery only, not ordering)
- Consumer uses `get_next_queued()` from `queue_state.py` to find what to play next
- Song URLs come from `queue_state.json`, not from the Kafka payload
- **Restart safety**: `queue_state.json` is file-persisted; Kafka messages are committed on receipt. On restart, consumer reads queue from file and plays in the saved order — no songs lost.
- `reset_stale_playing()` called on consumer startup — resets any item stuck as `"playing"` (from a crash) back to `"queued"` so it gets replayed

### Pre-fetch
- Consumer downloads the next song in a background thread while the current one plays
- Eliminates the silent gap between tracks
- Prefetch is discarded cleanly on skip or reorder

### History tab
- Shows last 100 played songs (newest first), with pagination and search
- Each row: source icon + clickable title, duration, played-at timestamp, Enqueue and +Playlist buttons
- Songs are logged to history when they **start** playing (not when they finish)

### Source detection & display
- API detects song source: `whatsapp` (via `X-Source: whatsapp` header set by the bridge), `web` (browser), `api` (direct API call)
- Source shown as icon only (WhatsApp SVG, 🌐, 🔧) in Queue and History tabs — no label text

### WhatsApp bridge enhancements
- Added `X-Source: whatsapp` header to all bridge enqueue requests (so the API correctly identifies WhatsApp-sourced songs)
- **Status commands**: texting `status`, `queue`, or `wait` returns now-playing info + queue length/wait time
- **Notify-on-play**: bridge sends a WhatsApp message to the requester when their song actually starts playing

### UI fixes and polish
- Queue table: collapsed from 10 columns to 5 (drag handle, title+source+status, duration, est. wait, skip)
- History table: collapsed from 7 columns to 4 (no uploader column, no separate source column)
- Song title is the clickable link in both tables (no separate link column)
- Manage Playlist modal: collapsed to fit in a single frame (no horizontal scroll)
- Duplicate prevention: `/enqueue` rejects a URL already in the queue with `status="queued"` (but allows re-enqueueing currently playing or skip_requested songs)
- **Fixed zombie skip_requested entries**: songs skipped before playing were stuck in `queue_state.json` forever (consumer's `get_next_queued()` filtered them out so `mark_done` was never called). Fixed by letting `get_next_queued()` return them so the consumer cleans them up.
- **Fixed stale "playing" mismatch**: after a crash, an item left as `"playing"` would cause the next song to also be marked "playing", making the UI show the wrong song. Fixed by `reset_stale_playing()` on startup.

### TTS pre-announcement
- Plays a short Hindi announcement before each queued song: _"अगला गाना है… [title]!"_
- Uses `edge-tts` (Microsoft Edge neural TTS, free, no API key, requires internet)
- Voice: `hi-IN-SwaraNeural` (female), rate `−10%` (slightly slower than default for clarity)
- Plays via ffplay (same pipeline as songs); fails silently if TTS is unavailable

### YouTube 403 fix
- YouTube's CDN was returning 403 on download URLs
- Fixed by using the Android player client: `extractor_args: {"youtube": {"player_client": ["android", "web"]}}`
- `cookies.txt` support: place a Netscape-format cookie export from Chrome in the project root for additional auth (optional but helps with persistent 403s)
- yt-dlp must be kept up to date: `pip install -U yt-dlp`

### Helper scripts added
- `test_tts.py` — plays the TTS announcement at 4 different speeds so you can pick the right rate without restarting the consumer
- `test_download.py` — diagnoses YouTube 403 errors: prints yt-dlp version, checks for cookies.txt, tests each player client

---

## Files worth knowing about

| File | Purpose |
|---|---|
| `README.md` | Main docs — setup, running, usage, validation, WhatsApp |
| `TAILSCALE.md` | Public internet via Tailscale Funnel |
| `config.py` | All env-driven settings |
| `real_time_validation/` | Single validation path for web/WhatsApp/Telegram: duplicate check, Redis rate limiting, content checks (age/category/duration), admin bypass |
| `producer_api.py` | FastAPI — all HTTP endpoints |
| `queue_state.py` | Shared file-based queue state (authoritative play order) |
| `consumer_worker.py` | Kafka consumer, playback loop, TTS announcement, pre-fetch |
| `playback.py` | yt-dlp download + ffplay playback, signal-file IPC |
| `default_playlist.py` | Admin-managed fallback playlists |
| `static/index.html` | Single-file web UI |
| `whatsapp-bridge/index.js` | WhatsApp → `/enqueue` bridge |
| `telegram-bridge/index.js` | Telegram → `/enqueue` bridge |
| `.queue_state.json` | Live queue (gitignored) |
| `.history.json` | Play history (gitignored) |
| `cookies.txt` | YouTube auth cookies for yt-dlp (gitignored, create manually) |
| `test_tts.py` | TTS speed sampler |
| `test_download.py` | YouTube download diagnostics |

---

## Outstanding / things to do next

1. **Git commits pending** — nothing from session 2 has been committed yet. Sensitive files (`.env`, `cookies.txt`, `auth_info_baileys/`, `requests.log`) are gitignored.
2. **Baileys RC** — still on `@whiskeysockets/baileys@7.0.0-rc13`. Worth upgrading to a stable 7.x once one ships.
3. **cookies.txt** — needs to be re-exported periodically when YouTube session cookies expire (weeks–months). Use the "Get cookies.txt LOCALLY" Chrome extension on `youtube.com`.
4. **yt-dlp updates** — YouTube changes frequently. Run `pip install -U yt-dlp` if 403s return.
5. `PycharmProjects\ytplayer` — safe to delete entirely.

---

## Future enhancements

Ideas for the next session, roughly in priority order:

### High value / quick wins
- ~~**WebSocket live updates**~~ — done: `GET /ws/now-playing` broadcasts the now-playing/up-next payload to every connected client once a second (song change/skip/pause/seek/queue change all surface within ~1s); frontend uses it instead of polling, with auto-reconnect. See [producer_api.py](producer_api.py) (`_now_playing_broadcast_loop`) and `static/index.html` (`connectNowPlayingSocket`).
- ~~**Per-requester song limit**~~ — done: `real_time_validation/rate_limiter.py`, Redis-backed, 3 songs/hour per phone/Telegram id/IP, configurable via `RATE_LIMIT_MAX_SONGS` / `RATE_LIMIT_WINDOW_SECONDS`.
- ~~**Audio volume normalization**~~ — done: every song plays through ffplay's `-af loudnorm` (single-pass, chained with the existing manual `volume` filter). Toggle via `NORMALIZE_VOLUME`, retarget via `LOUDNORM_TARGET_LUFS`.
- ~~**Crossfade**~~ — done: for consecutive real-queue songs, the next song's TTS announcement now fires `CROSSFADE_LEAD_SECONDS` (default 8) before the current one ends instead of after, overlapping the tail. Verified live: transition gap dropped to ~5ms. See `consumer_worker._announce_upcoming` / `playback.py`'s `on_near_end`. Not applied to the default-playlist fallback path (out of scope — its `interrupt_check` behavior already prioritizes cutting in a real song over a smooth transition).
- **Gujarati/English TTS option** — `gu-IN-DhwaniNeural` for Gujarati or `en-IN-NeerjaNeural` for Indian English, so the host can switch the announcement language to match the crowd.

### Queue & playlist features
- **Voting / upvote** — guests can upvote a queued song to move it up. Would need a lightweight persistence layer (or just bump it in `queue_state.json`).
- **Song dedupe across the event** — prevent the same song from playing twice in one session (not just once per queue; currently it can be re-enqueued after it plays).
- **Scheduled playlists** — activate a specific default playlist at a specific time (e.g., slow songs 8–9 PM, upbeat kite songs after). Config-driven cron or simple start/end time fields.
- **Shuffle mode for default playlists** — current round-robin is deterministic; a shuffle flag on the playlist would add variety.
- **Playlist import** — paste a YouTube playlist URL and import all songs as a default playlist in one go.

### Admin & monitoring
- **Admin dashboard tab** — song count played, top requesters (by phone/source), total playtime, most-requested songs.
- **Queue history export** — download all songs played in this session as a `.m3u` or JSON playlist.
- **Blacklist** — admin can ban a YouTube video ID or a phone number from enqueueing for the rest of the event.
- **Song request cap by duration** — reject if the queue's total estimated wait already exceeds X minutes (e.g., "queue is full, try again later").

### Infrastructure
- ~~**One-command start (Windows)**~~ — done: `start.bat` / `start.ps1` launch Docker Desktop if it isn't already running, wait for the daemon, then run `run.py` via `venv\Scripts\python.exe` — no manual Docker Desktop click-through or venv activation needed. `run.py` itself now also starts Kafka + Redis (`docker compose up -d`), the WhatsApp bridge, and the Telegram bridge alongside the API and consumer.
- **Auto-start on boot** — still open: the above is a single manual command, not true boot automation. Wrap `start.bat` in a Windows service or Scheduled Task (Task Scheduler, trigger "At log on") so the app comes up after a reboot with no one running anything by hand.
- **Docker-compose everything** — containerise the FastAPI app and consumer alongside Kafka/Redis so the whole stack starts with a single `docker compose up` (currently Kafka + Redis are containerized, but the API/consumer/bridges still run as local Python/Node processes via `run.py`).
- **PO token for yt-dlp** — YouTube's newer bot-detection requires a Proof-of-Origin token for some videos. Currently worked around with the Android player client, but a proper PO token would be more robust long-term. See https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide.
- ~~**Telegram bridge**~~ — done: `telegram-bridge/`, same pattern as the WhatsApp bridge but using the official Telegram Bot API.
- **Mobile-friendly UI** — the current UI works on mobile but the enqueue form and admin controls could be better optimised for small screens.
