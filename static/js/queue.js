// --- Queue (admin only) ------------------------------------------------

const refreshQueueBtn = document.getElementById('refresh-queue-btn');
const queueTable = document.getElementById('queue-table');
const queueBody = document.getElementById('queue-body');
const queueEmpty = document.getElementById('queue-empty');

let queueSelectedIds = new Set();

function updateBulkBar() {
  const bar = document.getElementById('queue-bulk-bar');
  const count = queueSelectedIds.size;
  if (count === 0) {
    bar.style.display = 'none';
    return;
  }
  bar.style.display = 'flex';
  document.getElementById('queue-bulk-count').textContent = `${count} selected`;
}

async function loadQueue() {
  if (!sessionStorage.getItem(AUTH_STORAGE_KEY)) return;

  try {
    const res = await fetch('/queue', { headers: getAuthHeader() });
    if (res.status === 401) {
      setLoggedOut();
      return;
    }
    const data = await res.json();
    const songs = data.queue || [];

    queueBody.innerHTML = '';
    queueSelectedIds.clear();
    updateBulkBar();

    if (songs.length === 0) {
      queueTable.style.display = 'none';
      queueEmpty.textContent = 'Queue is empty.';
      queueEmpty.style.display = 'block';
      document.getElementById('queue-clear-btn').style.display = 'none';
      return;
    }
    queueEmpty.style.display = 'none';
    queueTable.style.display = 'table';

    const queuedSongs = songs.filter(s => s.status === 'queued');
    document.getElementById('queue-clear-btn').style.display = queuedSongs.length > 0 ? 'inline-block' : 'none';

    let queueDragSrcIndex = null;
    const queuedSongIds = queuedSongs.map(s => s.id);

    // Select-all checkbox in header
    const selectAllCb = document.getElementById('queue-select-all');
    if (selectAllCb) {
      selectAllCb.checked = false;
      selectAllCb.onchange = () => {
        const cbs = queueBody.querySelectorAll('input[type="checkbox"]');
        cbs.forEach(cb => {
          cb.checked = selectAllCb.checked;
          if (selectAllCb.checked) queueSelectedIds.add(cb.dataset.songId);
          else queueSelectedIds.delete(cb.dataset.songId);
        });
        updateBulkBar();
      };
    }

    songs.forEach((song, index) => {
      const row = document.createElement('tr');
      row.dataset.index = index;
      row.dataset.songId = song.id;

      // Checkbox cell
      const cbCell = document.createElement('td');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.dataset.songId = song.id;
      cb.style.cssText = 'cursor:pointer;';
      cb.addEventListener('change', () => {
        if (cb.checked) queueSelectedIds.add(song.id);
        else queueSelectedIds.delete(song.id);
        updateBulkBar();
      });
      cbCell.appendChild(cb);
      row.appendChild(cbCell);

      const cell = (text, className) => {
        const td = document.createElement('td');
        td.textContent = text;
        if (className) td.className = className;
        return td;
      };

      const handleCell = document.createElement('td');
      if (song.status === 'queued') {
        handleCell.className = 'drag-handle';
        handleCell.textContent = '⠿';
        handleCell.title = 'Drag to reorder';
      }
      row.appendChild(handleCell);

      const titleTd = document.createElement('td');
      titleTd.style.cssText = 'white-space:normal; max-width:280px;';
      const titleLine = document.createElement('div');
      titleLine.style.cssText = 'display:flex; align-items:center; gap:6px; flex-wrap:wrap;';
      if (song.source) {
        const iconSpan = document.createElement('span');
        iconSpan.style.cssText = 'flex-shrink:0; line-height:1;';
        iconSpan.innerHTML = sourceIconHtml(song.source);
        titleLine.appendChild(iconSpan);
      }
      const titleLink = document.createElement('a');
      titleLink.href = song.url;
      titleLink.target = '_blank';
      titleLink.rel = 'noopener noreferrer';
      titleLink.textContent = song.title || '(unknown title)';
      titleLink.style.cssText = 'font-weight:500;';
      titleLine.appendChild(titleLink);
      if (song.status === 'playing') {
        const sb = document.createElement('span');
        sb.textContent = '▶ playing';
        sb.style.cssText = 'font-size:0.7rem; background:#e8f5e9; color:#2e7d32; padding:1px 5px; border-radius:8px; white-space:nowrap;';
        titleLine.appendChild(sb);
      }
      if (song.dedication) {
        const dedLine = document.createElement('div');
        dedLine.style.cssText = 'font-size:0.78rem; color:#7b1fa2; margin-top:2px; font-style:italic;';
        dedLine.textContent = `\u{1F49C} ${song.dedication}`;
        titleTd.appendChild(dedLine);
      }
      titleTd.appendChild(titleLine);
      row.appendChild(titleTd);

      row.appendChild(cell(song.duration));
      row.appendChild(cell(song.estimated_wait));

      const actionCell = document.createElement('td');
      const skipSongBtn = document.createElement('button');
      skipSongBtn.type = 'button';
      skipSongBtn.className = 'small';
      skipSongBtn.textContent = 'Skip';
      skipSongBtn.addEventListener('click', () => skipSong(song.id, skipSongBtn));
      actionCell.appendChild(skipSongBtn);
      row.appendChild(actionCell);

      if (song.status === 'queued') {
        const queuedIndex = queuedSongIds.indexOf(song.id);
        row.draggable = true;

        row.addEventListener('dragstart', (e) => {
          queueDragSrcIndex = queuedIndex;
          e.dataTransfer.effectAllowed = 'move';
          setTimeout(() => row.classList.add('dragging'), 0);
        });
        row.addEventListener('dragend', () => {
          row.classList.remove('dragging');
          queueBody.querySelectorAll('tr').forEach(r => r.classList.remove('drag-over'));
        });
        row.addEventListener('dragover', (e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = 'move';
          queueBody.querySelectorAll('tr[draggable]').forEach((r, i) => {
            r.classList.toggle('drag-over', i === queuedIndex);
          });
        });
        row.addEventListener('drop', (e) => {
          e.preventDefault();
          queueBody.querySelectorAll('tr').forEach(r => r.classList.remove('drag-over'));
          if (queueDragSrcIndex === null || queueDragSrcIndex === queuedIndex) return;
          const newOrder = [...queuedSongIds];
          const [moved] = newOrder.splice(queueDragSrcIndex, 1);
          newOrder.splice(queuedIndex, 0, moved);
          queueDragSrcIndex = null;
          sendQueueReorder(newOrder);
        });
      }

      queueBody.appendChild(row);
    });
  } catch (err) {
    queueEmpty.textContent = 'Failed to load queue: ' + err.message;
    queueEmpty.style.display = 'block';
    queueTable.style.display = 'none';
  }
}

async function sendQueueReorder(newOrder) {
  try {
    const res = await fetch('/queue/reorder', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({ song_ids: newOrder }),
    });
    if (res.status === 401) { setLoggedOut(); return; }
    if (res.ok) await loadQueue();
  } catch (_) {}
}

async function skipSong(songId, buttonEl) {
  buttonEl.disabled = true;
  try {
    const res = await fetch(`/skip/${encodeURIComponent(songId)}`, {
      method: 'POST',
      headers: getAuthHeader(),
    });
    if (res.status === 401) {
      setLoggedOut();
      return;
    }
    await loadQueue();
    loadNowPlaying();
  } catch (err) {
    buttonEl.disabled = false;
  }
}

refreshQueueBtn.addEventListener('click', loadQueue);

// Bulk skip selected
document.getElementById('queue-bulk-skip-btn').addEventListener('click', async () => {
  if (queueSelectedIds.size === 0) return;
  const ids = [...queueSelectedIds];
  try {
    const res = await fetch('/queue/skip-multiple', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({ song_ids: ids }),
    });
    if (res.status === 401) { setLoggedOut(); return; }
    await loadQueue();
    loadNowPlaying();
  } catch (_) {}
});

// Clear entire queue
document.getElementById('queue-clear-btn').addEventListener('click', async () => {
  if (!confirm('Remove all queued songs? The currently playing song will continue.')) return;
  try {
    const res = await fetch('/queue/clear', {
      method: 'POST',
      headers: getAuthHeader(),
    });
    if (res.status === 401) { setLoggedOut(); return; }
    await loadQueue();
  } catch (_) {}
});

// --- Skip current song (admin only) ------------------------------------

const skipBtn = document.getElementById('skip-btn');
const skipResult = document.getElementById('skip-result');

skipBtn.addEventListener('click', async () => {
  skipBtn.disabled = true;
  skipResult.className = '';
  skipResult.textContent = 'Skipping...';
  skipResult.style.display = 'block';

  try {
    const res = await fetch('/skip', { method: 'POST', headers: getAuthHeader() });
    if (res.status === 401) {
      setLoggedOut();
      return;
    }
    const data = await res.json();

    if (!res.ok) {
      skipResult.className = 'err';
      skipResult.textContent = formatError(data);
    } else {
      skipResult.className = 'ok';
      skipResult.textContent = 'Skip requested.';
      loadQueue();
      loadNowPlaying();
    }
  } catch (err) {
    skipResult.className = 'err';
    skipResult.textContent = 'Request failed: ' + err.message;
  } finally {
    skipBtn.disabled = false;
  }
});
