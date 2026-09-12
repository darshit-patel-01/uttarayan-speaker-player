// --- Enqueue (open to everyone) -----------------------------------------

const waitTimeBanner = document.getElementById('wait-time-banner');

let waitTimeState = { loaded: false, queueLength: 0, estimatedWaitSeconds: 0, dedicationsEnabled: true };

function formatDurationLocal(totalSeconds) {
  totalSeconds = Math.max(0, Math.round(totalSeconds));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const secs = totalSeconds % 60;
  if (hours) return `${hours}h ${minutes}m ${secs}s`;
  if (minutes) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

function renderWaitTimeBanner() {
  if (!waitTimeState.loaded) return;
  waitTimeBanner.textContent =
    waitTimeState.queueLength === 0
      ? 'Queue is empty — a new song would start right away.'
      : `A song enqueued right now would start in about ${formatDurationLocal(waitTimeState.estimatedWaitSeconds)} ` +
        `(${waitTimeState.queueLength} song${waitTimeState.queueLength === 1 ? '' : 's'} ahead).`;
}

async function loadWaitTime() {
  try {
    const res = await fetch('/wait-time');
    const data = await res.json();
    if (!res.ok) {
      waitTimeBanner.textContent = 'Could not load current wait time.';
      return;
    }
    waitTimeState = {
      loaded: true,
      queueLength: data.queue_length,
      estimatedWaitSeconds: data.estimated_wait_seconds,
      dedicationsEnabled: data.dedications_enabled !== false,
    };
    dedicationsEnabled = waitTimeState.dedicationsEnabled;
    renderWaitTimeBanner();
  } catch (err) {
    waitTimeBanner.textContent = 'Could not load current wait time.';
  }
}

loadWaitTime();
setInterval(loadWaitTime, 15000);
setInterval(() => {
  if (waitTimeState.loaded && waitTimeState.queueLength > 0) {
    waitTimeState.estimatedWaitSeconds = Math.max(0, waitTimeState.estimatedWaitSeconds - 1);
    renderWaitTimeBanner();
  }
}, 1000);

const form = document.getElementById('enqueue-form');
const urlInput = document.getElementById('url');
const submitBtn = document.getElementById('submit-btn');
const result = document.getElementById('result');

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const url = urlInput.value.trim();
  if (!url) return;
  result.style.display = 'none';
  // Shared path: dedication dialog (when enabled), POST, toast, tracking.
  // A rejected URL is left in the box so it can be corrected and retried.
  const song = await quickEnqueue({ url }, submitBtn);
  if (song) urlInput.value = '';
});

registerTabRefresh('enqueue', () => { loadWaitTime(); loadNowPlaying(); });
