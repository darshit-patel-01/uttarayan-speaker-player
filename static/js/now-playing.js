// --- Now playing / up next (public, always visible) ----------------------

let npState = { elapsed: 0, duration: 0, isPaused: false, isStopped: false, hasPlaying: false };
let seekBarDragging = false;
let _lastNotifiedSongId = null;

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

  const wrapper = document.createElement('div');
  wrapper.style.cssText = 'display:flex; align-items:center; gap:10px;';

  if (song.thumbnail) {
    const thumb = document.createElement('img');
    thumb.src = song.thumbnail;
    thumb.alt = '';
    thumb.style.cssText = 'width:64px; height:48px; object-fit:cover; border-radius:4px; flex-shrink:0;';
    wrapper.appendChild(thumb);
  }

  const info = document.createElement('div');
  info.style.cssText = 'min-width:0;';

  const titleRow = document.createElement('div');
  titleRow.style.cssText = 'display:flex; align-items:center; gap:6px; flex-wrap:wrap;';

  if (showIndicator && song.status !== 'downloading') {
    const indicator = document.createElement('span');
    indicator.className = 'np-playing-indicator';
    indicator.setAttribute('aria-hidden', 'true');
    for (let i = 0; i < 4; i++) {
      indicator.appendChild(document.createElement('span'));
    }
    titleRow.appendChild(indicator);
  }

  if (showIndicator && song.status === 'downloading') {
    const dlBadge = document.createElement('span');
    dlBadge.textContent = '⬇ downloading';
    dlBadge.style.cssText = 'font-size:0.7rem; background:#fff3e0; color:#e65100; padding:1px 5px; border-radius:8px; white-space:nowrap; animation:pulse 1.5s infinite;';
    titleRow.appendChild(dlBadge);
  }

  const link = document.createElement('a');
  link.href = song.url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.textContent = song.title || song.url;
  titleRow.appendChild(link);
  info.appendChild(titleRow);

  if (song.uploader) {
    const uploaderDiv = document.createElement('div');
    uploaderDiv.style.cssText = 'font-size:0.85rem; color:#666;';
    uploaderDiv.textContent = song.uploader;
    info.appendChild(uploaderDiv);
  }

  if (song.dedication_name || song.dedication) {
    const ded = document.createElement('div');
    ded.style.cssText = 'font-size:0.82rem; color:#7b1fa2; margin-top:2px; font-style:italic;';
    const parts = [];
    if (song.dedication_name) parts.push(`by ${song.dedication_name}`);
    if (song.dedication) parts.push(`for ${song.dedication}`);
    ded.textContent = `\u{1F49C} Dedicated ${parts.join(' ')}`;
    info.appendChild(ded);
  }

  if (song.source === 'playlist') {
    const badge = document.createElement('span');
    badge.className = 'np-source';
    badge.textContent = ' (default playlist)';
    info.appendChild(badge);
  }

  wrapper.appendChild(info);
  container.appendChild(wrapper);
}

// --- Browser notifications for web requesters ----------------------------

function _getTrackedSongIds() {
  try {
    return JSON.parse(sessionStorage.getItem('_enqueued_song_ids') || '[]');
  } catch (_) { return []; }
}

function _removeTrackedSongId(id) {
  const ids = _getTrackedSongIds().filter(i => i !== id);
  sessionStorage.setItem('_enqueued_song_ids', JSON.stringify(ids));
}

function _checkSongNotification(data) {
  if (!data.playing || !data.playing.id) return;
  if (data.playing.status === 'downloading') return;
  const playingId = data.playing.id;
  if (playingId === _lastNotifiedSongId) return;
  const tracked = _getTrackedSongIds();
  if (!tracked.includes(playingId)) return;
  _lastNotifiedSongId = playingId;
  _removeTrackedSongId(playingId);
  const title = data.playing.title || 'Your song';
  if (Notification.permission === 'granted') {
    new Notification('\u{1F3B5} Your song is playing!', {
      body: title,
      icon: data.playing.thumbnail || undefined,
    });
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

  _checkSongNotification(data);

  if (typeof waitTimeState !== 'undefined' && data.queue_length !== undefined) {
    waitTimeState.loaded = true;
    waitTimeState.queueLength = data.queue_length;
    waitTimeState.estimatedWaitSeconds = data.estimated_wait_seconds ?? 0;
    renderWaitTimeBanner();
  }
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
