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

// Blocked-user appeal: chatId -> { senderId, expiresAt }
const pendingAppeals = new Map();

// Welcome message sent on first interaction from a user
const WELCOME_MESSAGE = `🎵 *Welcome to the Song Queue!*

Here's how to request songs:

🔍 *Search for a song:*
   /search Tara Vina Shyam

▶️ *Play from search results:*
   /play 1
   /play 2 from Darsh for Mom

🔗 *Play a YouTube link directly:*
   play https://youtu.be/abc123
   play https://youtu.be/abc123 from Darsh for Mom

📊 *Other commands:*
   /status — what's playing & queue info
   /next — what's coming up next
   /history — last 5 songs played
   /my songs — your songs in the queue
   /cancel — cancel your last queued song
   /help — show this message again

💡 *Tips:*
• "from Name" adds your name to the request
• "for Person" dedicates the song to someone
• You'll get notified when your song starts playing!`;

const greeted = new Set(); // chatId — tracks who already got the welcome

// Song search: "/search <query>" or "search <query>" returns numbered results
const SEARCH_COMMAND_RE = /^\s*\/?search\s+(.+)/i;
const PLAY_NUMBER_RE = /^\s*\/?play\s+(\d+)(?:\s+from\s+(.+?))?(?:\s+for\s+(.+?))?$/i;
const searchSessions = new Map(); // chatId -> { results: [...], expiresAt }

async function handleSearchCommand(query) {
  try {
    const res = await fetch(`${BASE_URL}/search?q=${encodeURIComponent(query)}&limit=5`);
    if (!res.ok) return { error: "Search failed." };
    const data = await res.json();
    return { results: data.results || [] };
  } catch (err) {
    return { error: `Search failed: ${err.message}` };
  }
}

function formatSearchResults(results) {
  if (results.length === 0) return "No results found.";
  const lines = ["🔍 *Search results:*\n"];
  results.forEach((r, i) => {
    const dur = r.duration_fmt || "?";
    const views = r.views_fmt ? ` · ${r.views_fmt}` : "";
    lines.push(`*${i + 1}.* ${r.title || "(unknown)"}\n    ${r.uploader || ""} · ${dur}${views}`);
  });
  lines.push("\nReply */play 1* to enqueue a song.");
  return lines.join("\n");
}

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

async function handleHistoryCommand() {
  try {
    const res = await fetch(`${BASE_URL}/history?per_page=5`);
    const data = await res.json();
    const songs = data.songs || [];
    if (songs.length === 0) return "📜 No songs have been played yet.";
    const lines = ["📜 *Recently played:*\n"];
    songs.forEach((s, i) => {
      const dur = s.duration_fmt || "";
      lines.push(`${i + 1}. ${s.title || s.url}${dur ? ` (${dur})` : ""}`);
    });
    return lines.join("\n");
  } catch (err) {
    return `Couldn't fetch history: ${err.message}`;
  }
}

async function handleNextCommand() {
  try {
    const res = await fetch(`${BASE_URL}/now-playing`);
    const data = await res.json();
    const lines = [];
    if (data.playing) {
      lines.push(`🎵 *Now playing:* ${data.playing.title || data.playing.url}`);
    } else {
      lines.push("🔇 Nothing playing right now.");
    }
    if (data.next) {
      lines.push(`⏭ *Up next:* ${data.next.title || data.next.url}`);
    } else {
      lines.push("📭 Nothing queued next.");
    }
    return lines.join("\n");
  } catch (err) {
    return `Couldn't fetch queue: ${err.message}`;
  }
}

async function handleMySongsCommand(requesterId) {
  try {
    const res = await fetch(`${BASE_URL}/my-songs?requester_id=${encodeURIComponent(requesterId)}`);
    const data = await res.json();
    const songs = data.songs || [];
    if (songs.length === 0) return "🎶 You have no songs in the queue right now.";
    const lines = ["🎶 *Your songs in queue:*\n"];
    songs.forEach((s) => {
      const status = s.status === "playing" ? "▶️ Playing now" : `#${s.position_in_queue} — ~${s.estimated_wait} wait`;
      lines.push(`• ${s.title || s.url}\n   ${status}`);
    });
    return lines.join("\n");
  } catch (err) {
    return `Couldn't fetch your songs: ${err.message}`;
  }
}

async function handleCancelCommand(requesterId) {
  try {
    const res = await fetch(`${BASE_URL}/cancel-last?requester_id=${encodeURIComponent(requesterId)}`, { method: "POST" });
    const data = await res.json();
    if (data.cancelled) {
      return `🗑️ Cancelled: ${data.title}`;
    }
    return `❌ ${data.reason}`;
  } catch (err) {
    return `Couldn't cancel: ${err.message}`;
  }
}

// Same URL shapes producer_api.py accepts, so anything we forward is
// guaranteed to at least pass the "is this a YouTube URL" shape check.
// Only matches when "play" appears directly before the link — messages
// that just contain a bare link with no "play" in front are ignored, so
// people can share YouTube links in chat without accidentally queuing them.
const PLAY_YOUTUBE_URL_RE =
  /\bplay\s+((?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/watch\?v=[\w-]+(?:[&?][\w=&%.-]*)?|youtu\.be\/[\w-]+(?:\?[\w=&%.-]*)?|youtube\.com\/shorts\/[\w-]+(?:\?[\w=&%.-]*)?))(?:\s+from\s+(.+?))?(?:\s+for\s+(.+?))?$/gim;

function extractYoutubeUrls(text) {
  if (!text) return { urls: [], dedication: undefined, dedicationName: undefined };
  const urls = [];
  let dedication, dedicationName;
  for (const match of text.matchAll(PLAY_YOUTUBE_URL_RE)) {
    const url = match[1];
    urls.push(url.startsWith("http") ? url : `https://${url}`);
    if (!dedicationName && match[2]) {
      dedicationName = match[2].trim().slice(0, 100) || undefined;
    }
    if (!dedication && match[3]) {
      dedication = match[3].trim().slice(0, 100) || undefined;
    }
  }
  return { urls: [...new Set(urls)], dedication, dedicationName };
}

async function enqueueUrls(urls, { asAdmin = false, requesterId, dedication, dedicationName } = {}) {
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

  const body = { urls };
  if (dedication) body.dedication = dedication;
  if (dedicationName) body.dedication_name = dedicationName;

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

const bot = new TelegramBot(BOT_TOKEN, { polling: true });

bot.on("polling_error", (err) => console.error("Polling error:", err.message));

// Tell the API which bot we're actually signed in as, so the share QR/t.me
// link tracks this token instead of a handle hand-copied into the settings
// that goes stale after a new BotFather token.
async function registerOwnUsername(username) {
  if (!username) return;
  if (!ADMIN_USERNAME || !ADMIN_PASSWORD) {
    console.warn("ADMIN_USERNAME/ADMIN_PASSWORD not set — cannot auto-register the share link bot.");
    return;
  }
  const token = Buffer.from(`${ADMIN_USERNAME}:${ADMIN_PASSWORD}`).toString("base64");
  try {
    const res = await fetch(`${BASE_URL}/share/bridge-identity`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Basic ${token}` },
      body: JSON.stringify({ telegram_bot: username }),
    });
    if (!res.ok) {
      console.warn(`Share-link registration failed: HTTP ${res.status}`);
      return;
    }
    const data = await res.json();
    console.log(`Share link ${data.status === "updated" ? "updated to" : "already set to"} @${username}`);
  } catch (err) {
    console.warn(`Share-link registration failed: ${err.message}`);
  }
}

bot.getMe().then((me) => {
  console.log(`Telegram bridge connected as @${me.username}. Forwarding YouTube links to ${ENQUEUE_URL}`);
  registerOwnUsername(me.username);
});

// Poll every 10s for admin replies to deliver to blocked users
setInterval(async () => {
  try {
    const res = await fetch(`${BASE_URL}/messages/outbox?source=telegram`);
    if (!res.ok) return;
    const data = await res.json();
    for (const reply of data.replies || []) {
      try {
        await bot.sendMessage(reply.requester_id, `📩 Message from admin:\n\n${reply.text}`);
        await fetch(`${BASE_URL}/messages/outbox/${reply.id}/delivered`, { method: "POST" });
        await logLine(`REPLY delivered telegram_id=${reply.requester_id}`);
      } catch (err) {
        await logLine(`REPLY failed telegram_id=${reply.requester_id} error="${err.message}"`);
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
      const { chatId, title } = pendingNotifications.get(playingId);
      pendingNotifications.delete(playingId);
      await bot.sendMessage(chatId, `🎵 Your song is playing now!\n${title}`);
    }
  } catch (_) {}
}, 5000);

bot.on("message", async (msg) => {
  const text = msg.text || "";
  const chatId = msg.chat.id;

  // Check if this user has a pending appeal window
  const appeal = pendingAppeals.get(chatId);
  if (appeal && Date.now() < appeal.expiresAt) {
    const appealText = (text || "").trim().slice(0, 400);
    if (!appealText) return;
    pendingAppeals.delete(chatId);
    try {
      const appealRes = await fetch(`${BASE_URL}/messages/appeal`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source: "telegram",
          requester_id: appeal.senderId,
          text: appealText,
        }),
      });
      if (appealRes.ok) {
        await bot.sendMessage(chatId, "✅ Your message has been sent to the admin. Please wait for their response.");
      } else {
        await bot.sendMessage(chatId, "❌ Could not send your message. Server error.");
      }
    } catch (_) {
      await bot.sendMessage(chatId, "❌ Could not send your message. Please try again later.");
    }
    return;
  }
  // Expired appeal — clean up
  if (appeal) pendingAppeals.delete(chatId);

  // Send welcome message on first interaction
  if (!greeted.has(chatId)) {
    greeted.add(chatId);
    await bot.sendMessage(chatId, WELCOME_MESSAGE, { parse_mode: "Markdown" });
  }

  // Help command: "/help", "/menu", "/welcome"
  if (/^\s*\/?(help|menu|welcome)\s*$/i.test(text)) {
    await bot.sendMessage(chatId, WELCOME_MESSAGE, { parse_mode: "Markdown" });
    return;
  }

  // Status commands: "status", "queue", "wait" (with or without leading "/")
  if (STATUS_COMMAND_RE.test(text)) {
    const reply = await handleStatusCommand();
    await bot.sendMessage(chatId, reply);
    return;
  }

  // History command
  if (/^\s*\/?(history|recent)\s*$/i.test(text)) {
    const reply = await handleHistoryCommand();
    await bot.sendMessage(chatId, reply, { parse_mode: "Markdown" });
    return;
  }

  // Next command
  if (/^\s*\/?next\s*$/i.test(text)) {
    const reply = await handleNextCommand();
    await bot.sendMessage(chatId, reply, { parse_mode: "Markdown" });
    return;
  }

  // My songs command
  if (/^\s*\/?(my\s*songs?|mine)\s*$/i.test(text)) {
    const senderId = String(msg.from?.id || "");
    const reply = await handleMySongsCommand(senderId);
    await bot.sendMessage(chatId, reply, { parse_mode: "Markdown" });
    return;
  }

  // Cancel command
  if (/^\s*\/?cancel\s*$/i.test(text)) {
    const senderId = String(msg.from?.id || "");
    const reply = await handleCancelCommand(senderId);
    await bot.sendMessage(chatId, reply);
    return;
  }

  // Search command: "/search <query>" or "search <query>"
  const searchMatch = text.match(SEARCH_COMMAND_RE);
  if (searchMatch) {
    const query = searchMatch[1].trim();
    const senderId = String(msg.from?.id || "");
    await logLine(`SEARCH telegram_id=${senderId} query="${query}"`);
    const { results, error } = await handleSearchCommand(query);
    if (error) {
      await bot.sendMessage(chatId, error);
    } else {
      searchSessions.set(chatId, { results, expiresAt: Date.now() + 5 * 60 * 1000 });
      await bot.sendMessage(chatId, formatSearchResults(results), { parse_mode: "Markdown" });
    }
    return;
  }

  // Play by number from search results: "/play 1", "play 2 from Darsh for Mom"
  const playNumMatch = text.match(PLAY_NUMBER_RE);
  if (playNumMatch) {
    const session = searchSessions.get(chatId);
    if (session && Date.now() < session.expiresAt) {
      const idx = parseInt(playNumMatch[1], 10) - 1;
      if (idx >= 0 && idx < session.results.length) {
        const chosen = session.results[idx];
        const senderId = String(msg.from?.id || "");
        const asAdmin = ADMIN_TELEGRAM_IDS.includes(senderId);
        const dedicationName = playNumMatch[2]?.trim().slice(0, 100) || undefined;
        const dedication = playNumMatch[3]?.trim().slice(0, 100) || undefined;
        await logLine(`SEARCH_PLAY telegram_id=${senderId} admin=${asAdmin} pick=${idx + 1} url=${chosen.url}`);
        try {
          const data = await enqueueUrls([chosen.url], { asAdmin, requesterId: senderId, dedication, dedicationName });
          for (const song of data.enqueued || []) {
            if (song.id) pendingNotifications.set(song.id, { chatId, title: song.title || song.url });
          }
          const prefix = asAdmin ? "👑 Admin request (validation skipped)\n\n" : "";
          await bot.sendMessage(chatId, prefix + formatReply(data));
        } catch (err) {
          await bot.sendMessage(chatId, `Couldn't reach the song queue: ${err.message}`);
        }
        return;
      } else {
        await bot.sendMessage(chatId, `Pick a number between 1 and ${session.results.length}.`);
        return;
      }
    }
    // No active search session — fall through to URL matching
  }

  const { urls, dedication, dedicationName } = extractYoutubeUrls(text);
  if (urls.length === 0) return; // silently ignore messages with no "play <link>"

  const senderId = String(msg.from?.id || "");
  const asAdmin = ADMIN_TELEGRAM_IDS.includes(senderId);

  for (const url of urls) {
    await logLine(`REQUEST telegram_id=${senderId} admin=${asAdmin} url=${url}${dedicationName ? ` from="${dedicationName}"` : ""}${dedication ? ` for="${dedication}"` : ""}`);
  }

  try {
    const data = await enqueueUrls(urls, { asAdmin, requesterId: senderId, dedication, dedicationName });

    // Check if any rejection is a blocked-user message
    const blockedRej = (data.rejected || []).find((r) => r.reason && r.reason.startsWith("BLOCKED:"));
    if (blockedRej) {
      pendingAppeals.set(chatId, { senderId, expiresAt: Date.now() + 10 * 60 * 1000 });
    }

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
