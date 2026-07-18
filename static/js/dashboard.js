// --- Dashboard (admin only) ---------------------------------------------

const SOURCE_LABELS = { whatsapp: 'WhatsApp', telegram: 'Telegram', web: 'Web', ip: 'Web (IP)', api: 'API', unknown: 'Unknown' };
function sourceLabel(s) { return SOURCE_LABELS[s] || s; }

const SOURCE_COLORS = { whatsapp: '#25D366', telegram: '#229ED9', web: '#FF9933', ip: '#FF9933', api: '#9C6ADE', unknown: '#bbbbbb' };
const PIE_FALLBACK = ['#FF6B6B', '#4ECDC4', '#FFD93D', '#6A67CE', '#F78FB3', '#59C3C3'];
function sourceColor(source, i) { return SOURCE_COLORS[source] || PIE_FALLBACK[i % PIE_FALLBACK.length]; }

const REQUESTER_COLORS = ['#FF6B6B', '#4ECDC4', '#FFD93D', '#6A67CE', '#F78FB3', '#59C3C3'];

function renderDonutChart(pieEl, legendEl, wrapEl, emptyEl, slices, total, centerText, centerLabel, colorFn) {
  pieEl.innerHTML = '';
  legendEl.innerHTML = '';

  if (!total || slices.length === 0) {
    wrapEl.style.display = 'none';
    emptyEl.style.display = 'block';
    return;
  }
  wrapEl.style.display = 'flex';
  emptyEl.style.display = 'none';

  const size = 190, stroke = 30, r = (size - stroke) / 2, cx = size / 2, cy = size / 2;
  const C = 2 * Math.PI * r;
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${size} ${size}`);
  svg.setAttribute('width', size);
  svg.setAttribute('height', size);
  svg.classList.add('dashboard-pie-svg');

  const bg = document.createElementNS(NS, 'circle');
  bg.setAttribute('cx', cx); bg.setAttribute('cy', cy); bg.setAttribute('r', r);
  bg.setAttribute('fill', 'none'); bg.setAttribute('stroke', 'rgba(150,150,150,0.15)');
  bg.setAttribute('stroke-width', stroke);
  svg.appendChild(bg);

  let offset = 0;
  slices.forEach((slice, i) => {
    const frac = slice.value / total;
    const len = frac * C;
    const seg = document.createElementNS(NS, 'circle');
    seg.setAttribute('cx', cx); seg.setAttribute('cy', cy); seg.setAttribute('r', r);
    seg.setAttribute('fill', 'none');
    seg.setAttribute('stroke', colorFn(slice, i));
    seg.setAttribute('stroke-width', stroke);
    const gap = slices.length > 1 ? 1.5 : 0;
    seg.setAttribute('stroke-dasharray', `${Math.max(len - gap, 0)} ${C - Math.max(len - gap, 0)}`);
    seg.setAttribute('stroke-dashoffset', -offset);
    seg.setAttribute('transform', `rotate(-90 ${cx} ${cy})`);
    svg.appendChild(seg);
    offset += len;
  });

  const totalEl = document.createElementNS(NS, 'text');
  totalEl.setAttribute('x', cx); totalEl.setAttribute('y', cy - 2);
  totalEl.setAttribute('text-anchor', 'middle'); totalEl.setAttribute('dominant-baseline', 'middle');
  totalEl.setAttribute('class', 'pie-center-total');
  totalEl.textContent = centerText;
  svg.appendChild(totalEl);
  const lbl = document.createElementNS(NS, 'text');
  lbl.setAttribute('x', cx); lbl.setAttribute('y', cy + 20);
  lbl.setAttribute('text-anchor', 'middle'); lbl.setAttribute('dominant-baseline', 'middle');
  lbl.setAttribute('class', 'pie-center-label');
  lbl.textContent = centerLabel;
  svg.appendChild(lbl);
  pieEl.appendChild(svg);

  slices.forEach((slice, i) => {
    const pct = Math.round((slice.value / total) * 100);
    const legRow = document.createElement('div');
    legRow.className = 'pie-legend-row';
    const swatch = document.createElement('span');
    swatch.className = 'pie-legend-swatch';
    swatch.style.background = colorFn(slice, i);
    const name = document.createElement('span');
    name.className = 'pie-legend-name';
    name.textContent = slice.name;
    const meta = document.createElement('span');
    meta.className = 'pie-legend-meta';
    meta.textContent = `${slice.label} (${pct}%)`;
    legRow.appendChild(swatch);
    legRow.appendChild(name);
    legRow.appendChild(meta);
    legendEl.appendChild(legRow);
  });
}

function renderSourcePie(bySource, totalPlays) {
  const slices = bySource.map(row => ({ name: sourceLabel(row.source), value: row.plays, label: String(row.plays), source: row.source }));
  renderDonutChart(
    document.getElementById('dashboard-pie'),
    document.getElementById('dashboard-pie-legend'),
    document.getElementById('dashboard-pie-wrap'),
    document.getElementById('dashboard-pie-empty'),
    slices, totalPlays, String(totalPlays), 'songs played',
    (slice, i) => sourceColor(slice.source, i)
  );
}

function renderPlaytimePie(bySource, totalPlaytime) {
  const slices = bySource.map(row => ({ name: sourceLabel(row.source), value: row.playtime_seconds || 0, label: row.playtime || '0s', source: row.source }));
  const totalSeconds = slices.reduce((s, r) => s + r.value, 0);
  renderDonutChart(
    document.getElementById('dashboard-playtime-pie'),
    document.getElementById('dashboard-playtime-pie-legend'),
    document.getElementById('dashboard-playtime-pie-wrap'),
    document.getElementById('dashboard-playtime-pie-empty'),
    slices, totalSeconds, totalPlaytime, 'total playtime',
    (slice, i) => sourceColor(slice.source, i)
  );
}

function renderRequestersPie(requesters) {
  const top3 = requesters.filter(r => r.source === 'whatsapp' || r.source === 'telegram').slice(0, 3);
  const slices = top3.map((row, i) => ({ name: row.value || sourceLabel(row.source), value: row.count, label: String(row.count) }));
  const total = slices.reduce((s, r) => s + r.value, 0);
  renderDonutChart(
    document.getElementById('dashboard-requesters-pie'),
    document.getElementById('dashboard-requesters-pie-legend'),
    document.getElementById('dashboard-requesters-pie-wrap'),
    document.getElementById('dashboard-requesters-empty'),
    slices, total, String(total), 'requests',
    (slice, i) => REQUESTER_COLORS[i % REQUESTER_COLORS.length]
  );
}

let dashboardSongsAll = [];
let dashboardSongsPage = 1;
let dashboardStatsData = null;
const DASHBOARD_SONGS_PER_PAGE = 5;
const DASHBOARD_SONGS_MAX = 50;

function renderDashboardSongsPage() {
  const songBody = document.getElementById('dashboard-songs-body');
  const songTable = document.getElementById('dashboard-songs-table');
  const songEmpty = document.getElementById('dashboard-songs-empty');
  const paginationEl = document.getElementById('dashboard-songs-pagination');
  const pageInfoEl = document.getElementById('dashboard-songs-page-info');
  const prevBtn = document.getElementById('dashboard-songs-prev-btn');
  const nextBtn = document.getElementById('dashboard-songs-next-btn');

  songBody.innerHTML = '';
  const songs = dashboardSongsAll.slice(0, DASHBOARD_SONGS_MAX);

  if (songs.length === 0) {
    songTable.style.display = 'none';
    songEmpty.style.display = 'block';
    paginationEl.style.display = 'none';
    return;
  }

  songEmpty.style.display = 'none';
  songTable.style.display = 'table';

  const totalPages = Math.ceil(songs.length / DASHBOARD_SONGS_PER_PAGE);
  if (dashboardSongsPage > totalPages) dashboardSongsPage = totalPages;
  if (dashboardSongsPage < 1) dashboardSongsPage = 1;

  const start = (dashboardSongsPage - 1) * DASHBOARD_SONGS_PER_PAGE;
  const pageSlice = songs.slice(start, start + DASHBOARD_SONGS_PER_PAGE);

  pageSlice.forEach((row, i) => {
    const tr = document.createElement('tr');
    const rankTd = document.createElement('td'); rankTd.textContent = start + i + 1; tr.appendChild(rankTd);
    const titleTd = document.createElement('td');
    if (row.url) {
      const a = document.createElement('a');
      a.href = row.url; a.target = '_blank'; a.rel = 'noopener noreferrer';
      a.textContent = row.title || row.video_id;
      titleTd.appendChild(a);
    } else {
      titleTd.textContent = row.title || row.video_id;
    }
    tr.appendChild(titleTd);
    const countTd = document.createElement('td'); countTd.textContent = row.count; tr.appendChild(countTd);
    songBody.appendChild(tr);
  });

  if (totalPages > 1) {
    paginationEl.style.display = 'flex';
    pageInfoEl.textContent = `Page ${dashboardSongsPage} of ${totalPages} (${songs.length} songs)`;
    prevBtn.disabled = dashboardSongsPage <= 1;
    nextBtn.disabled = dashboardSongsPage >= totalPages;
  } else {
    paginationEl.style.display = 'none';
  }
}

document.getElementById('dashboard-songs-prev-btn').addEventListener('click', () => {
  dashboardSongsPage--;
  renderDashboardSongsPage();
});
document.getElementById('dashboard-songs-next-btn').addEventListener('click', () => {
  dashboardSongsPage++;
  renderDashboardSongsPage();
});

async function loadDashboard() {
  if (!sessionStorage.getItem(AUTH_STORAGE_KEY)) return;
  const resultEl = document.getElementById('dashboard-result');
  try {
    const res = await fetch('/stats', { headers: getAuthHeader() });
    if (res.status === 401) { setLoggedOut(); return; }
    const data = await res.json();
    dashboardStatsData = data;
    resultEl.textContent = '';

    renderSourcePie(data.by_source || [], data.total_plays ?? 0);
    renderPlaytimePie(data.by_source || [], data.total_playtime || '0s');
    renderRequestersPie(data.top_requesters || []);

    dashboardSongsAll = data.top_songs || [];
    dashboardSongsPage = 1;
    renderDashboardSongsPage();
  } catch (err) {
    resultEl.className = 'err';
    resultEl.textContent = 'Failed to load dashboard: ' + err.message;
    resultEl.style.display = 'block';
  }
}

document.getElementById('refresh-dashboard-btn').addEventListener('click', loadDashboard);

document.getElementById('export-stats-btn').addEventListener('click', () => {
  if (!dashboardStatsData) return;
  const d = dashboardStatsData;
  const lines = [];

  lines.push('Uttarayan Song Queue — Stats Export');
  lines.push(`Exported at,${new Date().toLocaleString()}`);
  lines.push(`Total plays,${d.total_plays ?? 0}`);
  lines.push(`Total playtime,${d.total_playtime || '0m'}`);
  lines.push('');

  lines.push('Plays By Source');
  lines.push('Source,Plays,Playtime');
  for (const row of d.by_source || []) {
    lines.push(`${sourceLabel(row.source)},${row.plays},${row.playtime || ''}`);
  }
  lines.push('');

  lines.push('Top Requesters');
  lines.push('Source,Requester,Requests,Playtime');
  for (const row of d.top_requesters || []) {
    lines.push(`${sourceLabel(row.source)},"${row.value || ''}",${row.count},${row.playtime || ''}`);
  }
  lines.push('');

  lines.push('Top 50 Most Requested Songs');
  lines.push('Rank,Title,Video ID,Times Played,URL');
  const songs = (d.top_songs || []).slice(0, 50);
  songs.forEach((row, i) => {
    const title = (row.title || '').replace(/"/g, '""');
    lines.push(`${i + 1},"${title}",${row.video_id || ''},${row.count},${row.url || ''}`);
  });

  const csv = lines.join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `uttarayan-stats-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
});
