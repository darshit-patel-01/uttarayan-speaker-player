// --- History (open to everyone) ------------------------------------------

let historyPage = 1;
let historyPerPage = 10;

function formatPlayedAt(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

async function buildPlaylistPopover(song, container) {
  container.innerHTML = '<span style="font-size:0.78rem;color:#888;">Loading playlists…</span>';
  try {
    const res = await fetch('/playlists', { headers: getAuthHeader() });
    if (!res.ok) { container.innerHTML = '<span style="font-size:0.78rem;color:#b71c1c;">Could not load playlists.</span>'; return; }
    const data = await res.json();
    const playlists = data.playlists || [];
    if (playlists.length === 0) { container.innerHTML = '<span style="font-size:0.78rem;color:#888;">No playlists yet.</span>'; return; }

    container.innerHTML = '';

    const form = document.createElement('div');
    form.style.cssText = 'display:flex;flex-direction:column;gap:4px;margin-bottom:6px;';
    const checkboxes = [];
    playlists.forEach(pl => {
      const label = document.createElement('label');
      label.style.cssText = 'display:flex;align-items:center;gap:5px;font-size:0.8rem;cursor:pointer;';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = pl.id;
      cb.dataset.name = pl.name;
      label.appendChild(cb);
      label.appendChild(document.createTextNode(pl.name));
      form.appendChild(label);
      checkboxes.push(cb);
    });
    container.appendChild(form);

    const addBtn = document.createElement('button');
    addBtn.type = 'button';
    addBtn.className = 'small';
    addBtn.textContent = 'Add to selected';
    const feedbackEl = document.createElement('div');
    feedbackEl.style.cssText = 'font-size:0.78rem;margin-top:3px;';

    addBtn.addEventListener('click', async () => {
      const selected = checkboxes.filter(cb => cb.checked);
      if (selected.length === 0) { feedbackEl.style.color = '#b71c1c'; feedbackEl.textContent = 'Select at least one playlist.'; return; }
      addBtn.disabled = true;
      feedbackEl.style.color = '#555';
      feedbackEl.textContent = 'Adding…';

      const results = await Promise.all(selected.map(async cb => {
        const r = await fetch(`/playlists/${encodeURIComponent(cb.value)}/songs`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
          body: JSON.stringify({ urls: [song.url] }),
        });
        const d = await r.json();
        return { name: cb.dataset.name, ok: r.ok && d.added && d.added.length > 0, rejected: d.rejected && d.rejected[0] };
      }));

      const ok = results.filter(r => r.ok).map(r => r.name);
      const fail = results.filter(r => !r.ok);
      let msg = ok.length ? `✓ Added to: ${ok.join(', ')}` : '';
      if (fail.length) msg += (msg ? ' | ' : '') + `✗ Failed: ${fail.map(r => r.name).join(', ')}`;
      feedbackEl.style.color = ok.length ? '#1b5e20' : '#b71c1c';
      feedbackEl.textContent = msg;
      addBtn.disabled = false;
    });

    container.appendChild(addBtn);
    container.appendChild(feedbackEl);
  } catch (_) {
    container.innerHTML = '<span style="font-size:0.78rem;color:#b71c1c;">Error loading playlists.</span>';
  }
}

async function loadHistory(page) {
  historyPage = page || historyPage;
  const loadingEl = document.getElementById('history-loading');
  const emptyEl = document.getElementById('history-empty');
  const tableEl = document.getElementById('history-table');
  const bodyEl = document.getElementById('history-body');
  const paginationEl = document.getElementById('history-pagination');
  const pageInfoEl = document.getElementById('history-page-info');
  const prevBtn = document.getElementById('history-prev-btn');
  const nextBtn = document.getElementById('history-next-btn');

  loadingEl.style.display = 'block';
  emptyEl.style.display = 'none';
  tableEl.style.display = 'none';
  paginationEl.style.display = 'none';

  try {
    const q = encodeURIComponent((document.getElementById('history-search')?.value || '').trim());
    const res = await fetch(`/history?page=${historyPage}&per_page=${historyPerPage}&q=${q}`);
    const data = await res.json();
    loadingEl.style.display = 'none';

    if (!data.songs || data.songs.length === 0) {
      emptyEl.style.display = 'block';
      return;
    }

    const isAdmin = !!sessionStorage.getItem(AUTH_STORAGE_KEY);
    bodyEl.innerHTML = '';
    const offset = (data.page - 1) * data.per_page;

    data.songs.forEach((song, i) => {
      const row = document.createElement('tr');

      const numCell = document.createElement('td');
      numCell.textContent = offset + i + 1;
      row.appendChild(numCell);

      const titleCell = document.createElement('td');
      titleCell.style.cssText = 'white-space:normal;';
      const titleLine = document.createElement('div');
      titleLine.style.cssText = 'display:flex; align-items:center; gap:6px; flex-wrap:wrap;';
      if (song.source) {
        const iconSpan = document.createElement('span');
        iconSpan.style.cssText = 'flex-shrink:0; line-height:1;';
        iconSpan.innerHTML = sourceIconHtml(song.source);
        titleLine.appendChild(iconSpan);
      }
      const link = document.createElement('a');
      link.href = song.url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = song.title || song.url;
      titleLine.appendChild(link);
      titleCell.appendChild(titleLine);
      row.appendChild(titleCell);

      const cell = (text) => { const td = document.createElement('td'); td.textContent = text || '—'; return td; };
      row.appendChild(cell(song.duration_fmt));
      row.appendChild(cell(formatPlayedAt(song.played_at)));

      const actionCell = document.createElement('td');
      actionCell.style.minWidth = '130px';

      const enqBtn = document.createElement('button');
      enqBtn.type = 'button';
      enqBtn.className = 'small';
      enqBtn.textContent = 'Enqueue';
      const enqResult = document.createElement('div');
      enqResult.className = 'history-enqueue-result';

      enqBtn.addEventListener('click', async () => {
        enqBtn.disabled = true;
        enqResult.className = 'history-enqueue-result';
        enqResult.textContent = 'Enqueueing…';
        try {
          const r = await fetch('/enqueue', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
            body: JSON.stringify({ urls: [song.url] }),
          });
          const d = await r.json();
          if (!r.ok) {
            enqResult.className = 'history-enqueue-result err';
            enqResult.textContent = formatError(d);
          } else if (d.enqueued && d.enqueued.length > 0) {
            enqResult.className = 'history-enqueue-result ok';
            enqResult.textContent = `✓ Queued! Wait: ${d.enqueued[0].estimated_wait}`;
            loadNowPlaying();
          } else {
            enqResult.className = 'history-enqueue-result err';
            enqResult.textContent = (d.rejected && d.rejected[0]) ? d.rejected[0].reason : 'Rejected.';
          }
        } catch (_) {
          enqResult.className = 'history-enqueue-result err';
          enqResult.textContent = 'Request failed.';
        } finally {
          enqBtn.disabled = false;
        }
      });

      actionCell.appendChild(enqBtn);
      actionCell.appendChild(enqResult);

      if (isAdmin) {
        const plBtn = document.createElement('button');
        plBtn.type = 'button';
        plBtn.className = 'small secondary';
        plBtn.style.marginTop = '4px';
        plBtn.textContent = '＋ Playlist';

        const plPanel = document.createElement('div');
        plPanel.style.cssText = 'display:none;margin-top:5px;padding:8px 10px;background:#f9f5ff;border:1px solid #d1c4e9;border-radius:6px;';
        let plLoaded = false;

        plBtn.addEventListener('click', async () => {
          const open = plPanel.style.display !== 'none';
          plPanel.style.display = open ? 'none' : 'block';
          if (!open && !plLoaded) {
            plLoaded = true;
            await buildPlaylistPopover(song, plPanel);
          }
        });

        actionCell.appendChild(document.createElement('br'));
        actionCell.appendChild(plBtn);
        actionCell.appendChild(plPanel);
      }

      row.appendChild(actionCell);
      bodyEl.appendChild(row);
    });

    tableEl.style.display = 'table';
    pageInfoEl.textContent = `Page ${data.page} of ${data.total_pages} (${data.total} songs)`;
    prevBtn.disabled = data.page <= 1;
    nextBtn.disabled = data.page >= data.total_pages;
    paginationEl.style.display = 'flex';

  } catch (err) {
    loadingEl.style.display = 'none';
    emptyEl.textContent = 'Failed to load history.';
    emptyEl.style.display = 'block';
  }
}

document.getElementById('history-per-page').addEventListener('change', (e) => {
  historyPerPage = parseInt(e.target.value);
  loadHistory(1);
});
document.getElementById('history-refresh-btn').addEventListener('click', () => loadHistory(1));
document.getElementById('history-search').addEventListener('input', () => loadHistory(1));
document.getElementById('history-prev-btn').addEventListener('click', () => loadHistory(historyPage - 1));
document.getElementById('history-next-btn').addEventListener('click', () => loadHistory(historyPage + 1));

document.querySelectorAll('.tab-btn').forEach(btn => {
  if (btn.dataset.tab === 'history') btn.addEventListener('click', () => loadHistory(1));
});
