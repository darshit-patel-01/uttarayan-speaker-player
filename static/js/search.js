// --- YouTube search + enqueue from results --------------------------------

const searchForm = document.getElementById('search-form');
const searchQuery = document.getElementById('search-query');
const searchBtn = document.getElementById('search-btn');
const searchResults = document.getElementById('search-results');

searchForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const q = searchQuery.value.trim();
  if (!q) return;

  searchBtn.disabled = true;
  searchBtn.textContent = 'Searching...';
  searchResults.innerHTML = '<div style="color:#888; font-size:0.85rem; padding:8px 0;">Searching YouTube...</div>';

  try {
    const res = await fetch(`/search?q=${encodeURIComponent(q)}&limit=5`);
    const data = await res.json();
    if (!res.ok) {
      searchResults.innerHTML = `<div class="err" style="display:block;">Search failed: ${data.detail || 'unknown error'}</div>`;
      return;
    }
    renderSearchResults(data.results || []);
  } catch (err) {
    searchResults.innerHTML = `<div class="err" style="display:block;">Search failed: ${err.message}</div>`;
  } finally {
    searchBtn.disabled = false;
    searchBtn.textContent = 'Search';
  }
});

function renderSearchResults(results) {
  searchResults.innerHTML = '';
  if (results.length === 0) {
    searchResults.innerHTML = '<div style="color:#888; font-size:0.85rem; padding:8px 0;">No results found.</div>';
    return;
  }

  for (const r of results) {
    const card = document.createElement('div');
    card.className = 'search-result-card';

    if (r.thumbnail) {
      const img = document.createElement('img');
      img.src = r.thumbnail;
      img.alt = '';
      card.appendChild(img);
    }

    const info = document.createElement('div');
    info.className = 'search-result-info';
    const title = document.createElement('div');
    title.className = 'search-result-title';
    title.textContent = r.title || '(unknown)';
    info.appendChild(title);
    const meta = document.createElement('div');
    meta.className = 'search-result-meta';
    const parts = [];
    if (r.uploader) parts.push(r.uploader);
    if (r.duration_fmt) parts.push(r.duration_fmt);
    if (r.views_fmt) parts.push(r.views_fmt);
    meta.textContent = parts.join(' · ');
    info.appendChild(meta);
    card.appendChild(info);

    const btn = document.createElement('button');
    btn.className = 'enqueue-pill';
    btn.textContent = 'Enqueue';
    btn.addEventListener('click', () => enqueueFromSearch(r, btn));
    card.appendChild(btn);

    searchResults.appendChild(card);
  }
}

async function enqueueFromSearch(song, btn) {
  // Shared path: dedication dialog (when enabled), POST, toast, tracking.
  const s = await quickEnqueue(song, btn);
  if (s) {
    document.getElementById('search-query').value = '';
    searchResults.innerHTML = '';
  }
}
