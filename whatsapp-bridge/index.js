import { Boom } from "@hapi/boom";
import {
  DisconnectReason,
  fetchLatestBaileysVersion,
  makeWASocket,
  useMultiFileAuthState,
} from "@whiskeysockets/baileys";
import { appendFile } from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";
import pino from "pino";
import qrcode from "qrcode-terminal";

try {
  process.loadEnvFile();
} catch {
  // .env is optional — ENQUEUE_URL falls back to localhost below.
}

const BASE_URL = (process.env.ENQUEUE_URL || "http://localhost:8000/enqueue").replace(/\/enqueue$/, "");
const ENQUEUE_URL = `${BASE_URL}/enqueue`;

// Single audit log: who (phone number) requested what (song URL) and what
// happened to it (queued / rejected / errored). One line per event, always
// timestamped, appended in order — so "who used this and what did they
// request" can be answered just by reading (or grepping) this file.
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const LOG_FILE = path.join(__dirname, "requests.log");

async function logLine(line) {
  const stamped = `${new Date().toISOString()} ${line}`;
  console.log(stamped);
  try {
    await appendFile(LOG_FILE, stamped + "\n");
  } catch (err) {
    console.error("Failed to write to requests.log:", err);
  }
}
const ADMIN_USERNAME = process.env.ADMIN_USERNAME || "";
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "";

// Numbers here get treated as admin: their song requests skip validation
// (age/category/duration) entirely, same as logging in as admin on the web
// form. Digits only, no "+" or country code — matched as a substring of the
// sender's JID, since WhatsApp doesn't always report the country code the
// same way (e.g. LID-based JIDs for non-contacts).
const ADMIN_PHONE_NUMBERS = (process.env.ADMIN_PHONE_NUMBERS || "")
  .split(",")
  .map((n) => n.replace(/\D/g, ""))
  .filter(Boolean);

// Pending play notifications: song_id -> { jid, title }
const pendingNotifications = new Map();

// Blocked-user appeal: jid -> { phone, expiresAt }
// After a blocked user gets the rejection message, their NEXT message
// (within 10 minutes) is forwarded to the admin as an appeal.
const pendingAppeals = new Map();

// Status command: reply with now-playing + queue info
const STATUS_COMMAND_RE = /^\s*(status|queue|wait)\s*$/i;

async function handleStatusCommand() {
  try {
    const [npRes, waitRes] = await Promise.all([
      fetch(`${BASE_URL}/now-playing`),
      fetch(`${BASE_URL}/wait-time`),
    ]);
    const np = await npRes.json();
    const wait = await waitRes.json();
    const lines = [];
    if (np.playing) {
      lines.push(`🎵 Now playing: ${np.playing.title || np.playing.url}`);
      if (np.playing.uploader) lines.push(`   by ${np.playing.uploader}`);
    } else {
      lines.push("🔇 Nothing playing right now.");
    }
    if (np.next) lines.push(`⏭ Up next: ${np.next.title || np.next.url}`);
    lines.push(
      wait.queue_length > 0
        ? `⏳ ${wait.queue_length} song(s) in queue — ~${wait.estimated_wait} wait`
        : "📭 Queue is empty — your song would start right away!"
    );
    return lines.join("\n");
  } catch (err) {
    return `Couldn't reach the queue: ${err.message}`;
  }
}

// Same URL shapes producer_api.py accepts, so anything we forward is
// guaranteed to at least pass the "is this a YouTube URL" shape check.
// Only matches when "play" appears directly before the link — messages
// that just contain a bare link with no "play" in front are ignored, so
// people can share YouTube links in chat without accidentally queuing them.
const PLAY_YOUTUBE_URL_RE =
  /\bplay\s+((?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/watch\?v=[\w-]+(?:[&?][\w=&%.-]*)?|youtu\.be\/[\w-]+(?:\?[\w=&%.-]*)?|youtube\.com\/shorts\/[\w-]+(?:\?[\w=&%.-]*)?))(?:\s+for\s+(.+?))?$/gim;

function extractYoutubeUrls(text) {
  if (!text) return { urls: [], dedication: undefined };
  const urls = [];
  let dedication;
  for (const match of text.matchAll(PLAY_YOUTUBE_URL_RE)) {
    const url = match[1];
    urls.push(url.startsWith("http") ? url : `https://${url}`);
    if (!dedication && match[2]) {
      dedication = match[2].trim().slice(0, 100) || undefined;
    }
  }
  return { urls: [...new Set(urls)], dedication };
}

function digitsOnly(jid) {
  return (jid || "").split("@")[0].split(":")[0].replace(/\D/g, "");
}

// Extracts the sender's bare phone number from a message, whether it came
// from a DM (remoteJid is the sender) or a group (participant is the
// sender, remoteJid is the group). WhatsApp's privacy layer often reports
// the sender as an opaque "...@lid" identifier instead of their real number
// (especially for non-contacts) — when that happens, resolve it back to a
// phone number via Baileys' LID<->PN mapping store.
async function senderPhoneNumber(sock, msg) {
  const jid = msg.key.participant || msg.key.remoteJid || "";

  if (jid.endsWith("@lid")) {
    try {
      const pn = await sock.signalRepository.lidMapping.getPNForLID(jid);
      if (pn) return digitsOnly(pn);
    } catch (err) {
      console.error("LID -> phone number resolution failed:", err);
    }
    // No mapping known yet (e.g. WhatsApp hasn't synced it to us) — fall
    // back to the LID's own digits, which won't match ADMIN_PHONE_NUMBERS
    // but at least keeps logging/identification consistent.
  }

  return digitsOnly(jid);
}

async function enqueueUrls(urls, { asAdmin = false, requesterId, dedication } = {}) {
  const headers = { "Content-Type": "application/json", "X-Source": "whatsapp" };
  if (requesterId) {
    headers["X-Requester-Id"] = requesterId;
  }
  if (asAdmin) {
    if (!ADMIN_USERNAME || !ADMIN_PASSWORD) {
      throw new Error(
        "Sender is an admin number but ADMIN_USERNAME/ADMIN_PASSWORD aren't set in .env"
      );
    }
    const token = Buffer.from(`${ADMIN_USERNAME}:${ADMIN_PASSWORD}`).toString("base64");
    headers.Authorization = `Basic ${token}`;
  }

  const body = { urls };
  if (dedication) body.dedication = dedication;

  const res = await fetch(ENQUEUE_URL, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data));
  }
  return data;
}

function formatReply(data) {
  const lines = [];
  for (const song of data.enqueued || []) {
    lines.push(
      `✅ Queued: ${song.title || song.url}\n` +
        `   Position ${song.position_in_queue}, starts in about ${song.estimated_wait}.`
    );
  }
  for (const rej of data.rejected || []) {
    lines.push(`❌ ${rej.url}\n   ${rej.reason}`);
  }
  return lines.join("\n\n") || "Nothing to report.";
}

async function start() {
  const { state, saveCreds } = await useMultiFileAuthState("auth_info_baileys");
  // The version bundled with the npm package goes stale quickly (WhatsApp
  // rejects old versions outright with a 405 during the handshake, before
  // any QR is even shown) — fetch the current one each time instead.
  const { version, isLatest } = await fetchLatestBaileysVersion();
  console.log(`Using WhatsApp Web version ${version.join(".")} (isLatest=${isLatest})`);

  const sock = makeWASocket({
    version,
    auth: state,
    logger: pino({ level: "silent" }),
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      console.log("Scan this QR code in WhatsApp: Settings > Linked Devices > Link a Device");
      qrcode.generate(qr, { small: true });
    }

    if (connection === "close") {
      const statusCode = new Boom(lastDisconnect?.error)?.output?.statusCode;
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
      console.log(`Connection closed. statusCode=${statusCode}`, lastDisconnect?.error);
      console.log(
        shouldReconnect
          ? "Reconnecting..."
          : "Logged out. Delete the auth_info_baileys/ folder and restart to link again."
      );
      if (shouldReconnect) start();
    } else if (connection === "open") {
      console.log(`WhatsApp bridge connected. Forwarding YouTube links to ${ENQUEUE_URL}`);
    }
  });

  // Poll every 10s for admin replies to deliver to blocked users
  setInterval(async () => {
    try {
      const res = await fetch(`${BASE_URL}/messages/outbox?source=whatsapp`);
      if (!res.ok) return;
      const data = await res.json();
      for (const reply of data.replies || []) {
        const targetJid = `${reply.requester_id}@s.whatsapp.net`;
        try {
          await sock.sendMessage(targetJid, {
            text: `📩 Message from admin:\n\n${reply.text}`,
          });
          await fetch(`${BASE_URL}/messages/outbox/${reply.id}/delivered`, { method: "POST" });
          await logLine(`REPLY delivered phone=${reply.requester_id}`);
        } catch (err) {
          await logLine(`REPLY failed phone=${reply.requester_id} error="${err.message}"`);
        }
      }
    } catch (_) {}
  }, 10000);

  // Poll every 5s to notify requesters when their song starts playing
  setInterval(async () => {
    if (pendingNotifications.size === 0) return;
    try {
      const res = await fetch(`${BASE_URL}/now-playing`);
      if (!res.ok) return;
      const data = await res.json();
      const playingId = data.playing?.id;
      if (playingId && pendingNotifications.has(playingId)) {
        const { jid: notifyJid, title } = pendingNotifications.get(playingId);
        pendingNotifications.delete(playingId);
        await sock.sendMessage(notifyJid, {
          text: `🎵 Your song is playing now!\n${title}`,
        });
      }
    } catch (_) {}
  }, 5000);

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;

    for (const msg of messages) {
      if (!msg.message || msg.key.fromMe) continue;

      const jid = msg.key.remoteJid;
      const text =
        msg.message.conversation || msg.message.extendedTextMessage?.text || "";

      // Check if this user has a pending appeal window
      await logLine(`APPEAL_DEBUG jid=${jid} pendingAppeals=[${[...pendingAppeals.keys()].join(",")}] text="${text.slice(0, 50)}"`);
      const appeal = pendingAppeals.get(jid);
      if (appeal && Date.now() < appeal.expiresAt) {
        const appealText = (text || "").trim().slice(0, 400);
        if (!appealText) {
          await logLine(`APPEAL_SKIP_EMPTY jid=${jid} — ignoring empty message (likely link preview)`);
          continue;
        }
        pendingAppeals.delete(jid);
        await logLine(`APPEAL_MATCH jid=${jid} phone=${appeal.phone} appealText="${appealText.slice(0, 50)}"`);
        try {
          const appealRes = await fetch(`${BASE_URL}/messages/appeal`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              source: "whatsapp",
              requester_id: appeal.phone,
              text: appealText,
            }),
          });
          await logLine(`APPEAL_RESPONSE status=${appealRes.status}`);
          if (appealRes.ok) {
            await sock.sendMessage(jid, {
              text: "✅ Your message has been sent to the admin. Please wait for their response.",
            });
          } else {
            const errBody = await appealRes.text();
            await logLine(`APPEAL_ERROR body=${errBody}`);
            await sock.sendMessage(jid, {
              text: "❌ Could not send your message. Server error.",
            });
          }
        } catch (err) {
          await logLine(`APPEAL_FETCH_ERROR error="${err.message}"`);
          await sock.sendMessage(jid, {
            text: "❌ Could not send your message. Please try again later.",
          });
        }
        continue;
      }
      // Expired appeal — clean up
      if (appeal) {
        await logLine(`APPEAL_EXPIRED jid=${jid}`);
        pendingAppeals.delete(jid);
      }

      // Status commands: "status", "queue", "wait"
      if (STATUS_COMMAND_RE.test(text)) {
        const reply = await handleStatusCommand();
        await sock.sendMessage(jid, { text: reply });
        continue;
      }

      const { urls, dedication } = extractYoutubeUrls(text);
      if (urls.length === 0) continue; // silently ignore messages with no "play <link>"

      const number = await senderPhoneNumber(sock, msg);
      const asAdmin = ADMIN_PHONE_NUMBERS.some((admin) => number.includes(admin));

      for (const url of urls) {
        await logLine(`REQUEST phone=${number} admin=${asAdmin} url=${url}${dedication ? ` dedication="${dedication}"` : ""}`);
      }

      try {
        const data = await enqueueUrls(urls, { asAdmin, requesterId: number, dedication });

        // Check if any rejection is a blocked-user message
        const blockedRej = (data.rejected || []).find((r) => r.reason && r.reason.startsWith("BLOCKED:"));
        if (blockedRej) {
          pendingAppeals.set(jid, { phone: number, expiresAt: Date.now() + 10 * 60 * 1000 });
          await logLine(`APPEAL_WINDOW_SET jid=${jid} phone=${number}`);
        }

        for (const song of data.enqueued || []) {
          await logLine(
            `RESULT queued phone=${number} admin=${asAdmin} url=${song.url} ` +
              `title="${song.title || ""}" position=${song.position_in_queue}`
          );
          // Register for a "now playing" notification
          if (song.id) {
            pendingNotifications.set(song.id, { jid, title: song.title || song.url });
          }
        }
        for (const rej of data.rejected || []) {
          await logLine(
            `RESULT rejected phone=${number} admin=${asAdmin} url=${rej.url} reason="${rej.reason}"`
          );
        }

        const prefix = asAdmin ? "👑 Admin request (validation skipped)\n\n" : "";
        await sock.sendMessage(jid, { text: prefix + formatReply(data) });
      } catch (err) {
        await logLine(
          `RESULT error phone=${number} admin=${asAdmin} urls=${urls.join(",")} error="${err.message}"`
        );
        await sock.sendMessage(jid, {
          text: `Couldn't reach the song queue: ${err.message}`,
        });
      }
    }
  });
}

start();
