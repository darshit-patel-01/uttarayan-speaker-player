// --- Enqueue (open to everyone) -----------------------------------------

const waitTimeBanner = document.getElementById('wait-time-banner');

let waitTimeState = { loaded: false, queueLength: 0, estimatedWaitSeconds: 0 };

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
    };
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
const dedicationInput = document.getElementById('dedication');
const submitBtn = document.getElementById('submit-btn');
const result = document.getElementById('result');

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const url = urlInput.value.trim();
  if (!url) return;

  const dedication = dedicationInput.value.trim() || undefined;

  submitBtn.disabled = true;
  result.className = '';
  result.textContent = 'Enqueuing...';
  result.style.display = 'block';

  try {
    const res = await fetch('/enqueue', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({ urls: [url], dedication }),
    });
    const data = await res.json();

    if (!res.ok) {
      result.className = 'err';
      result.textContent = formatError(data);
    } else if (data.enqueued.length > 0) {
      const song = data.enqueued[0];
      result.className = 'ok';
      result.textContent =
        `Queued! ID: ${song.id}\n` +
        `${song.title || '(unknown title)'} — ${song.uploader || '(unknown uploader)'}\n` +
        `Position ${song.position_in_queue}, duration ${song.duration}, ` +
        `estimated wait ${song.estimated_wait}.`;
      urlInput.value = '';
      dedicationInput.value = '';
      loadQueue();
      loadWaitTime();
      loadNowPlaying();
    } else {
      const rejection = data.rejected[0];
      result.className = 'err';
      result.textContent = rejection ? rejection.reason : 'Rejected.';
      urlInput.value = '';
    }
  } catch (err) {
    result.className = 'err';
    result.textContent = 'Request failed: ' + err.message;
  } finally {
    submitBtn.disabled = false;
  }
});
