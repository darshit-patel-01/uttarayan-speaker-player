// --- Now playing / up next (public, always visible) ----------------------

let npState = { elapsed: 0, duration: 0, isPaused: false, isStopped: false, hasPlaying: false };
let seekBarDragging = false;

function formatTime(secs) {
  secs = Math.max(0, Math.round(secs || 0));
  const m = Math.floor(secs / 60), s = secs % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function renderNowPlayingRow(containerId, song, emptyText, showIndicator) {
  const container = document.getElementById(containerId);
  container.innerHTML = '';
  if (!song) {
    container.textContent = emptyText;
    return;
  }

  if (showIndicator) {
    const indicator = document.createElement('span');
    indicator.className = 'np-playing-indicator';
    indicator.setAttribute('aria-hidden', 'true');
    for (let i = 0; i < 4; i++) {
      indicator.appendChild(document.createElement('span'));
    }
    container.appendChild(indicator);
    container.appendChild(document.createTextNode(' '));
  }

  const link = document.createElement('a');
  link.href = song.url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.textContent = song.title || song.url;
  container.appendChild(link);

  if (song.uploader) {
    container.appendChild(document.createTextNode(` — ${song.uploader}`));
  }

  if (song.dedication) {
    const ded = document.createElement('div');
    ded.style.cssText = 'font-size:0.82rem; color:#7b1fa2; margin-top:2px; font-style:italic;';
    ded.textContent = `\u{1F49C} Dedicated: ${song.dedication}`;
    container.appendChild(ded);
  }

  if (song.source === 'playlist') {
    const badge = document.createElement('span');
    badge.className = 'np-source';
    badge.textContent = ' (default playlist)';
    container.appendChild(badge);
  }
}

function updateAdminProgressUI() {
  if (!sessionStorage.getItem(AUTH_STORAGE_KEY)) return;
  const elapsed = npState.elapsed;
  const duration = npState.duration || 0;
  const bar = document.getElementById('np-seek-bar');
  const elapsedEl = document.getElementById('np-elapsed');
  const durationEl = document.getElementById('np-duration');
  const pauseBtn = document.getElementById('np-pause-resume-btn');

  elapsedEl.textContent = formatTime(elapsed);
  durationEl.textContent = duration ? formatTime(duration) : '--:--';

  if (!seekBarDragging) {
    bar.max = duration || 100;
    bar.value = elapsed;
    bar.disabled = !npState.hasPlaying || !duration;
  }

  if (npState.isStopped) {
    pauseBtn.textContent = '▶ Resume';
  } else if (npState.isPaused) {
    pauseBtn.textContent = '▶ Resume';
  } else {
    pauseBtn.textContent = '⏸ Pause';
  }
  pauseBtn.disabled = !npState.hasPlaying;
  document.getElementById('np-skip-now-btn').disabled = !npState.hasPlaying;
  document.getElementById('np-backward-btn').disabled = !npState.hasPlaying || !duration;
  document.getElementById('np-forward-btn').disabled = !npState.hasPlaying || !duration;
}

function applyNowPlayingData(data) {
  renderNowPlayingRow('np-playing-text', data.playing, 'Nothing playing right now.', true);
  renderNowPlayingRow('np-next-text', data.next, 'Nothing queued next.', false);

  if (data.playing) {
    npState.hasPlaying = true;
    npState.elapsed = data.playing.elapsed_seconds ?? 0;
    npState.duration = data.playing.duration_seconds ?? 0;
    npState.isPaused = data.playing.is_paused ?? false;
  } else {
    npState.hasPlaying = false;
    npState.elapsed = 0;
    npState.duration = 0;
    npState.isPaused = false;
  }
  updateAdminProgressUI();
}

async function loadNowPlaying() {
  try {
    const res = await fetch('/now-playing');
    applyNowPlayingData(await res.json());
  } catch (err) {
    document.getElementById('np-playing-text').textContent = 'Could not load.';
    document.getElementById('np-next-text').textContent = 'Could not load.';
  }
}

loadNowPlaying();

// --- Live now-playing updates over WebSocket ------------------------------
let nowPlayingSocket = null;
let nowPlayingReconnectDelay = 1000;

function connectNowPlayingSocket() {
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const socket = new WebSocket(`${protocol}//${location.host}/ws/now-playing`);
  nowPlayingSocket = socket;

  socket.addEventListener('open', () => {
    nowPlayingReconnectDelay = 1000;
  });

  socket.addEventListener('message', (event) => {
    try {
      applyNowPlayingData(JSON.parse(event.data));
    } catch (err) {}
  });

  socket.addEventListener('close', () => {
    if (nowPlayingSocket !== socket) return;
    setTimeout(connectNowPlayingSocket, nowPlayingReconnectDelay);
    nowPlayingReconnectDelay = Math.min(nowPlayingReconnectDelay * 2, 15000);
  });

  socket.addEventListener('error', () => socket.close());
}

connectNowPlayingSocket();

setInterval(() => {
  if (!npState.hasPlaying || npState.isPaused || npState.isStopped || seekBarDragging) return;
  if (!sessionStorage.getItem(AUTH_STORAGE_KEY)) return;
  npState.elapsed = Math.min(npState.elapsed + 1, npState.duration || Infinity);
  updateAdminProgressUI();
}, 1000);

// --- Admin playback controls ---------------------------------------------

async function adminAction(path, body) {
  const opts = { method: 'POST', headers: { ...getAuthHeader() } };
  if (body) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 401) { setLoggedOut(); return false; }
  return res.ok;
}

document.getElementById('np-pause-resume-btn').addEventListener('click', async () => {
  if (npState.isPaused || npState.isStopped) {
    await adminAction('/resume');
    npState.isPaused = false;
    npState.isStopped = false;
  } else {
    await adminAction('/pause');
    npState.isPaused = true;
  }
  updateAdminProgressUI();
  setTimeout(loadNowPlaying, 300);
});

document.getElementById('np-skip-now-btn').addEventListener('click', async () => {
  await adminAction('/skip');
  setTimeout(() => { loadNowPlaying(); loadQueue(); }, 600);
});

document.getElementById('np-backward-btn').addEventListener('click', async () => {
  const target = Math.max(0, npState.elapsed - 10);
  await adminAction('/seek', { seconds: target });
  npState.elapsed = target;
  npState.isPaused = false;
  updateAdminProgressUI();
});

document.getElementById('np-forward-btn').addEventListener('click', async () => {
  const target = Math.min(npState.duration || 0, npState.elapsed + 10);
  await adminAction('/seek', { seconds: target });
  npState.elapsed = target;
  npState.isPaused = false;
  updateAdminProgressUI();
});

// --- Volume slider -------------------------------------------------------

const volumeBar = document.getElementById('np-volume-bar');
const volumePct = document.getElementById('np-volume-pct');
let volumeThrottleTimer = null;
let volumeThrottlePending = false;

async function loadVolume() {
  try {
    const res = await fetch('/volume', { headers: getAuthHeader() });
    if (!res.ok) return;
    const data = await res.json();
    volumeBar.value = data.volume;
    volumePct.textContent = data.volume + '%';
  } catch (_) {}
}

volumeBar.addEventListener('input', () => {
  volumePct.textContent = volumeBar.value + '%';
  if (volumeThrottleTimer) {
    volumeThrottlePending = true;
    return;
  }
  const sendVolume = async () => {
    await adminAction('/volume', { volume: parseInt(volumeBar.value) });
    if (volumeThrottlePending) {
      volumeThrottlePending = false;
      volumeThrottleTimer = setTimeout(sendVolume, 100);
    } else {
      volumeThrottleTimer = null;
    }
  };
  volumeThrottleTimer = setTimeout(sendVolume, 0);
});

// --- Seek bar: drag to scrub, release to seek
const seekBar = document.getElementById('np-seek-bar');
seekBar.addEventListener('mousedown', () => { seekBarDragging = true; });
seekBar.addEventListener('touchstart', () => { seekBarDragging = true; });
seekBar.addEventListener('input', () => {
  npState.elapsed = parseFloat(seekBar.value);
  document.getElementById('np-elapsed').textContent = formatTime(npState.elapsed);
});
seekBar.addEventListener('change', async () => {
  seekBarDragging = false;
  const target = parseFloat(seekBar.value);
  await adminAction('/seek', { seconds: target });
  npState.elapsed = target;
  npState.isPaused = false;
  updateAdminProgressUI();
});
