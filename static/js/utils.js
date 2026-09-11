const WA_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="#25D366" style="vertical-align:middle;margin-right:3px;flex-shrink:0"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>`;

function sourceIconHtml(source) {
  if (source === 'whatsapp') return WA_SVG;
  if (source === 'web') return '🌐';
  if (source === 'api') return '🔧';
  return '';
}

function makeSourceCell(source) {
  const td = document.createElement('td');
  if (source === 'whatsapp') {
    td.innerHTML = WA_SVG + 'WhatsApp';
  } else if (source === 'web') {
    td.textContent = '🌐 Web';
  } else if (source === 'api') {
    td.textContent = '🔧 API';
  } else {
    td.textContent = source || '—';
  }
  return td;
}

function showToast(message, type = 'success', duration = 3000) {
  const existing = document.querySelector('.uttarayan-toast');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.className = `uttarayan-toast ${type}`;
  toast.dataset.shout = type === 'error' ? 'E Lapet!' : 'Kai Po Che!';
  const icon = type === 'error' ? '\u{274C}' : '\u{1FA81}';
  toast.innerHTML =
    `<span class="toast-kites" aria-hidden="true"><span class="tk tk-a">\u{1FA81}</span><span class="tk tk-b">\u{1FA81}</span></span>` +
    `<span style="font-size:1.3rem">${icon}</span><span>${message}</span>`;
  document.body.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('out');
    toast.addEventListener('animationend', () => toast.remove());
  }, duration);
}

function formatError(data) {
  if (typeof data.detail === 'string') return data.detail;
  if (Array.isArray(data.detail)) {
    return data.detail.map(e => e.msg || JSON.stringify(e)).join('; ');
  }
  return JSON.stringify(data);
}

// --- Shared quick-enqueue (prompts for a dedication when enabled) --------

let dedicationsEnabled = true;

const _qeModal = document.getElementById('quick-enqueue-modal');
const _qeTitle = document.getElementById('quick-enqueue-title');
const _qeName = document.getElementById('quick-enqueue-name');
const _qeDedication = document.getElementById('quick-enqueue-dedication');
let _qeResolve = null;

function _qeSettle(value) {
  const resolve = _qeResolve;
  _qeResolve = null;
  if (resolve) resolve(value);
}

document.getElementById('quick-enqueue-form').addEventListener('submit', (e) => {
  e.preventDefault();
  const value = {
    dedication_name: _qeName.value.trim() || undefined,
    dedication: _qeDedication.value.trim() || undefined,
  };
  _qeModal.close();
  _qeSettle(value);
});

document.getElementById('quick-enqueue-cancel-btn').addEventListener('click', () => _qeModal.close());
_qeModal.addEventListener('close', () => _qeSettle(null));

function askDedication(title) {
  return new Promise(resolve => {
    _qeName.value = '';
    _qeDedication.value = '';
    _qeTitle.textContent = title || '';
    _qeResolve = resolve;
    _qeModal.showModal();
  });
}

async function quickEnqueue(song, btn) {
  let dedicationFields = {};
  if (dedicationsEnabled) {
    const answer = await askDedication(song.title || song.url);
    if (answer === null) return;
    dedicationFields = answer;
  }

  const originalText = btn ? btn.textContent : null;
  if (btn) { btn.disabled = true; btn.textContent = 'Adding…'; }
  try {
    const res = await fetch('/enqueue', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({ urls: [song.url], ...dedicationFields }),
    });
    const data = await res.json();
    if (!res.ok) {
      showToast(formatError(data), 'error');
    } else if (data.enqueued && data.enqueued.length > 0) {
      const s = data.enqueued[0];
      showToast(`${s.title || 'Song'} queued! Position #${s.position_in_queue}, wait ${s.estimated_wait}`, 'success', 4000);
      loadQueue();
      loadWaitTime();
      loadNowPlaying();
    } else {
      const rejection = data.rejected && data.rejected[0];
      showToast(rejection ? rejection.reason : 'Rejected.', 'error');
    }
  } catch (err) {
    showToast('Request failed: ' + err.message, 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = originalText; }
  }
}

function makeEnqueueButton(song, className = 'small') {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = className;
  btn.textContent = 'Enqueue';
  btn.addEventListener('click', () => quickEnqueue(song, btn));
  return btn;
}

// --- Tabs ----------------------------------------------------------------

const tabButtons = document.querySelectorAll('.tab-btn');
const tabPanels = document.querySelectorAll('.tab-panel');
const _tabRefreshers = {};

function registerTabRefresh(tabName, fn) { _tabRefreshers[tabName] = fn; }

function switchTab(tabName) {
  tabButtons.forEach(btn => btn.classList.toggle('active', btn.dataset.tab === tabName));
  tabPanels.forEach(panel => panel.classList.toggle('active', panel.dataset.tab === tabName));
  const refresher = _tabRefreshers[tabName];
  if (refresher) refresher();
}

tabButtons.forEach(btn => btn.addEventListener('click', () => switchTab(btn.dataset.tab)));

switchTab('enqueue');

// --- Back to top button --------------------------------------------------
const _backToTopBtn = document.getElementById('back-to-top');
window.addEventListener('scroll', () => {
  _backToTopBtn.classList.toggle('visible', window.scrollY > 300);
});
_backToTopBtn.addEventListener('click', () => {
  window.scrollTo({ top: 0, behavior: 'smooth' });
});
