// --- Blacklist (admin only) ---------------------------------------------

const blacklistResult = document.getElementById('blacklist-result');
const blacklistVideoForm = document.getElementById('blacklist-video-form');
const blacklistVideoInput = document.getElementById('blacklist-video-input');
const blacklistVideosTable = document.getElementById('blacklist-videos-table');
const blacklistVideosBody = document.getElementById('blacklist-videos-body');
const blacklistVideosEmpty = document.getElementById('blacklist-videos-empty');
const blacklistRequesterForm = document.getElementById('blacklist-requester-form');
const blacklistRequesterSource = document.getElementById('blacklist-requester-source');
const blacklistRequesterInput = document.getElementById('blacklist-requester-input');
const blacklistRequestersTable = document.getElementById('blacklist-requesters-table');
const blacklistRequestersBody = document.getElementById('blacklist-requesters-body');
const blacklistRequestersEmpty = document.getElementById('blacklist-requesters-empty');

const REQUESTER_SOURCE_LABELS = { whatsapp: 'WhatsApp', telegram: 'Telegram', ip: 'Web (IP)' };

function showBlacklistMessage(text, isError) {
  blacklistResult.className = isError ? 'err' : 'ok';
  blacklistResult.textContent = text;
  blacklistResult.style.display = 'block';
}

function makeRemoveButton(onClick) {
  const td = document.createElement('td');
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'secondary';
  btn.textContent = 'Remove';
  btn.addEventListener('click', onClick);
  td.appendChild(btn);
  return td;
}

async function loadBlacklist() {
  if (!sessionStorage.getItem(AUTH_STORAGE_KEY)) return;
  try {
    const res = await fetch('/blacklist', { headers: getAuthHeader() });
    if (res.status === 401) { setLoggedOut(); return; }
    const data = await res.json();

    blacklistVideosBody.innerHTML = '';
    const videoIds = data.video_ids || [];
    if (videoIds.length === 0) {
      blacklistVideosTable.style.display = 'none';
      blacklistVideosEmpty.style.display = 'block';
    } else {
      blacklistVideosEmpty.style.display = 'none';
      blacklistVideosTable.style.display = 'table';
      for (const vid of videoIds) {
        const row = document.createElement('tr');
        const idCell = document.createElement('td');
        const link = document.createElement('a');
        link.href = `https://youtu.be/${vid}`;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = vid;
        idCell.appendChild(link);
        row.appendChild(idCell);
        row.appendChild(makeRemoveButton(() => removeBlacklistVideo(vid)));
        blacklistVideosBody.appendChild(row);
      }
    }

    blacklistRequestersBody.innerHTML = '';
    const requesters = data.requesters || [];
    if (requesters.length === 0) {
      blacklistRequestersTable.style.display = 'none';
      blacklistRequestersEmpty.style.display = 'block';
    } else {
      blacklistRequestersEmpty.style.display = 'none';
      blacklistRequestersTable.style.display = 'table';
      for (const r of requesters) {
        const row = document.createElement('tr');
        const typeCell = document.createElement('td');
        typeCell.textContent = REQUESTER_SOURCE_LABELS[r.source] || r.source;
        const valueCell = document.createElement('td');
        valueCell.textContent = r.value;
        row.appendChild(typeCell);
        row.appendChild(valueCell);
        row.appendChild(makeRemoveButton(() => removeBlacklistRequester(r.source, r.value)));
        blacklistRequestersBody.appendChild(row);
      }
    }

    await loadRecentRequesters(requesters);
  } catch (err) {
    showBlacklistMessage('Failed to load blacklist: ' + err.message, true);
  }
}

async function loadRecentRequesters(blockedRequesters) {
  const recentBody = document.getElementById('blacklist-recent-body');
  const recentTable = document.getElementById('blacklist-recent-table');
  const recentEmpty = document.getElementById('blacklist-recent-empty');
  recentBody.innerHTML = '';

  try {
    const res = await fetch('/stats', { headers: getAuthHeader() });
    if (!res.ok) { recentEmpty.style.display = 'block'; recentTable.style.display = 'none'; return; }
    const data = await res.json();
    const allRequesters = data.top_requesters || [];
    const filtered = allRequesters.filter(r => r.source === 'whatsapp' || r.source === 'telegram' || r.source === 'web' || r.source === 'ip');

    const grouped = new Map();
    for (const r of filtered) {
      const key = r.value || '—';
      if (grouped.has(key)) {
        const existing = grouped.get(key);
        existing.count += r.count;
        if (!existing.sources.includes(r.source)) existing.sources.push(r.source);
      } else {
        grouped.set(key, { value: r.value, source: r.source, sources: [r.source], count: r.count });
      }
    }
    const recent = Array.from(grouped.values()).sort((a, b) => b.count - a.count);

    if (recent.length === 0) {
      recentTable.style.display = 'none';
      recentEmpty.style.display = 'block';
      return;
    }
    recentEmpty.style.display = 'none';
    recentTable.style.display = 'table';

    const blockedSet = new Set((blockedRequesters || []).map(r => `${r.source}:${r.value}`));

    for (const r of recent) {
      const row = document.createElement('tr');
      const blSource = r.source === 'web' ? 'ip' : r.source;
      const isBlocked = r.sources.some(s => blockedSet.has(`${(s === 'web' ? 'ip' : s)}:${r.value}`));

      const typeCell = document.createElement('td');
      typeCell.textContent = r.sources.map(s => REQUESTER_SOURCE_LABELS[s] || s).join(', ');
      row.appendChild(typeCell);

      const valueCell = document.createElement('td');
      valueCell.textContent = r.value || '—';
      row.appendChild(valueCell);

      const countCell = document.createElement('td');
      countCell.textContent = r.count;
      row.appendChild(countCell);

      const actionCell = document.createElement('td');
      if (isBlocked) {
        const badge = document.createElement('span');
        badge.textContent = 'Blocked';
        badge.style.cssText = 'color:#b71c1c; font-weight:600; font-size:0.85rem;';
        actionCell.appendChild(badge);
      } else {
        const blockBtn = document.createElement('button');
        blockBtn.type = 'button';
        blockBtn.className = 'small';
        blockBtn.style.background = '#c62828';
        blockBtn.textContent = 'Block';
        blockBtn.addEventListener('click', async () => {
          blockBtn.disabled = true;
          blockBtn.textContent = 'Blocking…';
          const sources = r.sources.map(s => s === 'web' ? 'ip' : s);
          const uniqueSources = [...new Set(sources)];
          for (const src of uniqueSources) {
            await addBlacklistRequester(src, r.value, { skipReload: true });
          }
          loadBlacklist();
        });
        actionCell.appendChild(blockBtn);
      }
      row.appendChild(actionCell);
      recentBody.appendChild(row);
    }
  } catch (_) {
    recentTable.style.display = 'none';
    recentEmpty.style.display = 'block';
  }
}

async function addBlacklistVideo(videoId) {
  const res = await fetch('/blacklist/video', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
    body: JSON.stringify({ video_id: videoId }),
  });
  if (res.status === 401) { setLoggedOut(); return; }
  const data = await res.json();
  if (!res.ok) { showBlacklistMessage(formatError(data), true); return; }
  showBlacklistMessage(
    data.status === 'already_present' ? `Video ${data.video_id} was already blocked.` : `Blocked video ${data.video_id}.`,
    false
  );
  loadBlacklist();
}

async function removeBlacklistVideo(videoId) {
  const res = await fetch(`/blacklist/video/${encodeURIComponent(videoId)}`, {
    method: 'DELETE',
    headers: getAuthHeader(),
  });
  if (res.status === 401) { setLoggedOut(); return; }
  if (res.ok) { showBlacklistMessage(`Unblocked video ${videoId}.`, false); loadBlacklist(); }
}

async function addBlacklistRequester(source, value, { skipReload = false } = {}) {
  const res = await fetch('/blacklist/requester', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
    body: JSON.stringify({ source, value }),
  });
  if (res.status === 401) { setLoggedOut(); return; }
  const data = await res.json();
  if (!res.ok) { showBlacklistMessage(formatError(data), true); return; }
  const label = REQUESTER_SOURCE_LABELS[source] || source;
  showBlacklistMessage(
    data.status === 'already_present' ? `${label} ${value} was already blocked.` : `Blocked ${label} ${value}.`,
    false
  );
  if (!skipReload) loadBlacklist();
}

async function removeBlacklistRequester(source, value) {
  const params = new URLSearchParams({ source, value });
  const res = await fetch(`/blacklist/requester?${params.toString()}`, {
    method: 'DELETE',
    headers: getAuthHeader(),
  });
  if (res.status === 401) { setLoggedOut(); return; }
  if (res.ok) { showBlacklistMessage(`Unblocked ${value}.`, false); loadBlacklist(); }
}

blacklistVideoForm.addEventListener('submit', (e) => {
  e.preventDefault();
  const val = blacklistVideoInput.value.trim();
  if (!val) return;
  addBlacklistVideo(val);
  blacklistVideoInput.value = '';
});

blacklistRequesterForm.addEventListener('submit', (e) => {
  e.preventDefault();
  const val = blacklistRequesterInput.value.trim();
  if (!val) return;
  addBlacklistRequester(blacklistRequesterSource.value, val);
  blacklistRequesterInput.value = '';
});
