# WhatsApp bridge

Watches a WhatsApp number for messages containing YouTube links and forwards
each one to the main app's `POST /enqueue` endpoint — so people can add a
song to the queue by just texting a link, no need to open the web page.

**Important:** this connects to WhatsApp the same way WhatsApp Web does (via
[Baileys](https://github.com/WhiskeySockets/Baileys)), not through Meta's
official Business API. That means:

- No business approval or paid number needed — just scan a QR code with any
  WhatsApp account, like linking a new device.
- It is **not officially sanctioned by WhatsApp**. The number could get
  rate-limited or banned, and this can break whenever WhatsApp updates their
  app. Use a spare/throwaway number, not one you depend on.

## Setup

```bash
cd whatsapp-bridge
npm install
```

By default it forwards to `http://localhost:8000/enqueue` (the main app
running locally on this same machine). To point it elsewhere, copy
`.env.example` to `.env` and set `ENQUEUE_URL`.

## Running

```bash
npm start
```

On first run it prints a QR code in the terminal:

1. Open WhatsApp on the phone you want to use → **Settings → Linked
   Devices → Link a Device**.
2. Scan the QR code shown in the terminal.

Once connected, it stays logged in — the session is saved to
`auth_info_baileys/` (never commit this folder; it's equivalent to being
logged into that WhatsApp account). You won't need to scan again unless you
delete that folder or WhatsApp logs the device out.

Leave this running (e.g. in its own terminal, alongside `python run.py`)
for it to keep forwarding messages.

## How it behaves

- Only messages of the form `play <youtube link>` are picked up — the word
  "play" (case-insensitive) has to come right before the link. This is
  intentional: it lets people share YouTube links in chat normally without
  every link accidentally getting queued.
  - Matches: `play https://youtu.be/dQw4w9WgXcQ`, `Play https://www.youtube.com/watch?v=...`
  - Ignored: a bare link with no "play" in front, or "play" appearing
    somewhere else in the message with no link right after it.
- A matched link gets forwarded to `/enqueue`, exactly like pasting it into
  the web form — same validation (music-only, not age-restricted, under 2
  hours), same queue.
- The bridge replies in the same chat with the result: ✅ queued with
  position/wait time, or ❌ with the rejection reason.
- Everything else (no "play" + link pattern found) is silently ignored.
- Numbers in `ADMIN_PHONE_NUMBERS` (see Setup below) skip validation
  entirely, same as logging in as admin on the web form. Everyone else goes
  through the same public validation as the web form's "Enqueuing Song" tab.

## Request log

Every matched request and its outcome is appended to `requests.log` (in
this folder) — one line per event, timestamped, e.g.:

```
2026-07-09T02:14:03.881Z REQUEST phone=15551234567 admin=true url=https://youtu.be/XlFebTyooag
2026-07-09T02:14:04.512Z RESULT queued phone=15551234567 admin=true url=https://youtu.be/XlFebTyooag title="..." position=1
```

Use it to see who used the bridge and what they requested — e.g.
`grep admin=true requests.log` for admin-bypass requests, or
`grep 15551234567 requests.log` for everything from one number. It's
gitignored (contains phone numbers) and grows indefinitely — rotate or
delete it periodically if that matters to you.
