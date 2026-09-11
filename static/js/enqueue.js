// --- Enqueue (open to everyone) -----------------------------------------

const waitTimeBanner = document.getElementById('wait-time-banner');
const dedicationFields = document.querySelector('.dedication-fields');

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
    dedicationFields.style.display = dedicationsEnabled ? '' : 'none';
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
const dedicationNameInput = document.getElementById('dedication-name');
const dedicationInput = document.getElementById('dedication');
const submitBtn = document.getElementById('submit-btn');
const result = document.getElementById('result');

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const url = urlInput.value.trim();
  if (!url) return;

  const dedicationName = dedicationNameInput.value.trim() || undefined;
  const dedication = dedicationInput.value.trim() || undefined;

  submitBtn.disabled = true;
  result.style.display = 'none';

  try {
    const res = await fetch('/enqueue', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({ urls: [url], dedication, dedication_name: dedicationName }),
    });
    const data = await res.json();

    if (!res.ok) {
      showToast(formatError(data), 'error');
    } else if (data.enqueued.length > 0) {
      const song = data.enqueued[0];
      showToast(
        `${song.title || 'Song'} queued! Position #${song.position_in_queue}, wait ${song.estimated_wait}`,
        'success', 4000
      );
      urlInput.value = '';
      dedicationNameInput.value = '';
      dedicationInput.value = '';

      if (song.id) {
        try {
          const tracked = JSON.parse(sessionStorage.getItem('_enqueued_song_ids') || '[]');
          tracked.push(song.id);
          sessionStorage.setItem('_enqueued_song_ids', JSON.stringify(tracked));
        } catch (_) {}
        if ('Notification' in window && Notification.permission === 'default') {
          Notification.requestPermission();
        }
      }

      loadQueue();
      loadWaitTime();
      loadNowPlaying();
    } else {
      const rejection = data.rejected[0];
      showToast(rejection ? rejection.reason : 'Rejected.', 'error');
      urlInput.value = '';
    }
  } catch (err) {
    showToast('Request failed: ' + err.message, 'error');
  } finally {
    submitBtn.disabled = false;
  }
});

registerTabRefresh('enqueue', () => { loadWaitTime(); loadNowPlaying(); });
