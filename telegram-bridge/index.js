import TelegramBot from "node-telegram-bot-api";
import { appendFile } from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";

try {
  process.loadEnvFile();
} catch {
  // .env is optional — ENQUEUE_URL falls back to localhost below.
}

const BASE_URL = (process.env.ENQUEUE_URL || "http://localhost:8000/enqueue").replace(/\/enqueue$/, "");
const ENQUEUE_URL = `${BASE_URL}/enqueue`;

const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || "";
if (!BOT_TOKEN) {
  console.error("TELEGRAM_BOT_TOKEN is not set. Create a bot via @BotFather and set it in .env.");
  process.exit(1);
}

// Single audit log: who (Telegram user id) requested what (song URL) and
// what happened to it (queued / rejected / errored). One line per event,
// timestamped, appended in order — same shape as whatsapp-bridge/requests.log.
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

// Numeric Telegram user IDs (from @userinfobot or similar) that get treated
// as admin: their song requests skip validation (age/category/duration)
// entirely, same as logging in as admin on the web form. Telegram doesn't
// expose phone numbers to bots by default, so IDs are the stable identifier
// here (unlike ADMIN_PHONE_NUMBERS in whatsapp-bridge).
const ADMIN_TELEGRAM_IDS = (process.env.ADMIN_TELEGRAM_IDS || "")
  .split(",")
  .map((n) => n.trim())
  .filter(Boolean);

// Pending play notifications: song_id -> { chatId, title }
const pendingNotifications = new Map();

// Status command: reply with now-playing + queue info
const STATUS_COMMAND_RE = /^\s*\/?(status|queue|wait)\s*$/i;

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
  /\bplay\s+((?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/watch\?v=[\w-]+(?:[&?][\w=&%.-]*)?|youtu\.be\/[\w-]+(?:\?[\w=&%.-]*)?|youtube\.com\/shorts\/[\w-]+(?:\?[\w=&%.-]*)?))/gi;

function extractYoutubeUrls(text) {
  if (!text) return [];
  const urls = [];
  for (const match of text.matchAll(PLAY_YOUTUBE_URL_RE)) {
    const url = match[1];
    urls.push(url.startsWith("http") ? url : `https://${url}`);
  }
  return [...new Set(urls)];
}

async function enqueueUrls(urls, { asAdmin = false, requesterId } = {}) {
  const headers = { "Content-Type": "application/json", "X-Source": "telegram" };
  if (requesterId) {
    headers["X-Requester-Id"] = requesterId;
  }
  if (asAdmin) {
    if (!ADMIN_USERNAME || !ADMIN_PASSWORD) {
      throw new Error(
        "Sender is an admin id but ADMIN_USERNAME/ADMIN_PASSWORD aren't set in .env"
      );
    }
    const token = Buffer.from(`${ADMIN_USERNAME}:${ADMIN_PASSWORD}`).toString("base64");
    headers.Authorization = `Basic ${token}`;
  }

  const res = await fetch(ENQUEUE_URL, {
    method: "POST",
    headers,
    body: JSON.stringify({ urls }),
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

const bot = new TelegramBot(BOT_TOKEN, { polling: true });

bot.on("polling_error", (err) => console.error("Polling error:", err.message));

bot.getMe().then((me) => {
  console.log(`Telegram bridge connected as @${me.username}. Forwarding YouTube links to ${ENQUEUE_URL}`);
});

// Poll every 5s to notify requesters when their song starts playing
setInterval(async () => {
  if (pendingNotifications.size === 0) return;
  try {
    const res = await fetch(`${BASE_URL}/now-playing`);
    if (!res.ok) return;
    const data = await res.json();
    const playingId = data.playing?.id;
    if (playingId && pendingNotifications.has(playingId)) {
      const { chatId, title } = pendingNotifications.get(playingId);
      pendingNotifications.delete(playingId);
      await bot.sendMessage(chatId, `🎵 Your song is playing now!\n${title}`);
    }
  } catch (_) {}
}, 5000);

bot.on("message", async (msg) => {
  const text = msg.text || "";
  const chatId = msg.chat.id;

  // Status commands: "status", "queue", "wait" (with or without leading "/")
  if (STATUS_COMMAND_RE.test(text)) {
    const reply = await handleStatusCommand();
    await bot.sendMessage(chatId, reply);
    return;
  }

  const urls = extractYoutubeUrls(text);
  if (urls.length === 0) return; // silently ignore messages with no "play <link>"

  const senderId = String(msg.from?.id || "");
  const asAdmin = ADMIN_TELEGRAM_IDS.includes(senderId);

  for (const url of urls) {
    await logLine(`REQUEST telegram_id=${senderId} admin=${asAdmin} url=${url}`);
  }

  try {
    const data = await enqueueUrls(urls, { asAdmin, requesterId: senderId });

    for (const song of data.enqueued || []) {
      await logLine(
        `RESULT queued telegram_id=${senderId} admin=${asAdmin} url=${song.url} ` +
          `title="${song.title || ""}" position=${song.position_in_queue}`
      );
      // Register for a "now playing" notification
      if (song.id) {
        pendingNotifications.set(song.id, { chatId, title: song.title || song.url });
      }
    }
    for (const rej of data.rejected || []) {
      await logLine(
        `RESULT rejected telegram_id=${senderId} admin=${asAdmin} url=${rej.url} reason="${rej.reason}"`
      );
    }

    const prefix = asAdmin ? "👑 Admin request (validation skipped)\n\n" : "";
    await bot.sendMessage(chatId, prefix + formatReply(data));
  } catch (err) {
    await logLine(
      `RESULT error telegram_id=${senderId} admin=${asAdmin} urls=${urls.join(",")} error="${err.message}"`
    );
    await bot.sendMessage(chatId, `Couldn't reach the song queue: ${err.message}`);
  }
});
