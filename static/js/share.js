// --- QR code share modal with WhatsApp / Telegram / Web tabs -------------

const qrModal = document.getElementById('qr-modal');
const qrImgWrap = document.getElementById('qr-img-wrap');
const qrUrlEl = document.getElementById('qr-url');
const qrInstructions = document.getElementById('qr-instructions');
const qrAdminConfig = document.getElementById('qr-admin-config');

const QR_INSTRUCTIONS = {
  whatsapp: 'Open your phone camera and point it at this code — it opens a WhatsApp chat. Send a song name or YouTube link to add it to the queue.',
  telegram: 'Open your phone camera and point it at this code — it opens our Telegram bot. Send a song name or YouTube link to add it to the queue.',
  web: 'Open your phone camera and point it at this code to open the song queue in your browser.',
};
const qrConfigResult = document.getElementById('qr-config-result');

let _qrActiveTarget = 'whatsapp';
let _qrShareConfig = {};
let _qrCurrentUrl = '';

function _isAdmin() {
  return !!sessionStorage.getItem(AUTH_STORAGE_KEY);
}

function _showBridgeWarning(bridge, message) {
  const existing = document.getElementById('bridge-warning-toast');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.id = 'bridge-warning-toast';
  toast.style.cssText = 'position:fixed;top:20px;left:50%;transform:translateX(-50%);z-index:10001;background:#fff3e0;color:#e65100;border:1px solid #ffb74d;border-radius:8px;padding:14px 20px;max-width:420px;font-size:0.85rem;box-shadow:0 4px 16px rgba(0,0,0,0.15);line-height:1.4;';
  toast.innerHTML = `<strong>${bridge} number updated</strong><br>${message}<br><button onclick="this.parentElement.remove()" style="margin-top:8px;padding:4px 14px;border:1px solid #e65100;border-radius:4px;background:transparent;color:#e65100;cursor:pointer;font-size:0.8rem;">Got it</button>`;
  document.body.appendChild(toast);
}

async function _loadShareConfig() {
  try {
    const res = await fetch('/share/config');
    if (res.ok) _qrShareConfig = await res.json();
  } catch (_) {}
}

function _qrTargetUrl(target) {
  if (target === 'whatsapp') {
    const num = _qrShareConfig.whatsapp_number;
    return num ? `https://wa.me/${num}?text=search%20` : '';
  }
  if (target === 'telegram') {
    const bot = _qrShareConfig.telegram_bot;
    return bot ? `https://t.me/${bot}` : '';
  }
  return _qrShareConfig.web_url || window.location.origin;
}

function _renderQr(target) {
  _qrActiveTarget = target;
  const url = _qrTargetUrl(target);
  _qrCurrentUrl = url;
  qrImgWrap.innerHTML = '';

  if (!url) {
    const msg = document.createElement('div');
    msg.style.cssText = 'color:#888; font-size:0.85rem; padding:40px 0;';
    msg.textContent = _isAdmin()
      ? `Not configured yet — enter the details above and click Save.`
      : `${target === 'whatsapp' ? 'WhatsApp' : 'Telegram'} sharing is not set up yet.`;
    qrImgWrap.appendChild(msg);
    qrUrlEl.textContent = '';
    qrInstructions.textContent = '';
    return;
  }

  qrInstructions.textContent = QR_INSTRUCTIONS[target] || QR_INSTRUCTIONS.web;

  const img = document.createElement('img');
  img.src = `/share/qr?target=${target}&_t=${Date.now()}`;
  img.alt = 'QR code';
  img.style.cssText = 'width:220px; height:220px;';
  qrImgWrap.appendChild(img);
  qrUrlEl.textContent = url;

  if (target === 'web') {
    const isPublic = !!_qrShareConfig.public_url && url === _qrShareConfig.public_url;
    const note = document.createElement('div');
    note.style.cssText = `font-size:0.75rem; margin-top:4px; color:${isPublic ? '#2e7d32' : '#8a6d3b'};`;
    note.textContent = isPublic
      ? '\u{1F310} Public link — works from any network'
      : '\u{1F512} Local network only — turn on the Tailscale funnel to share publicly';
    qrUrlEl.appendChild(note);
  }

  const autoDetected = target === 'whatsapp'
    ? _qrShareConfig.whatsapp_auto_detected
    : target === 'telegram' ? _qrShareConfig.telegram_auto_detected : false;
  if (autoDetected) {
    const note = document.createElement('div');
    note.style.cssText = 'font-size:0.75rem; margin-top:4px; color:#2e7d32;';
    note.textContent = '\u{2713} Auto-detected from the connected bridge';
    qrUrlEl.appendChild(note);
  }
}

function _showConfigPanel(target) {
  document.querySelectorAll('.qr-config-panel').forEach(p => p.style.display = 'none');
  const panel = document.getElementById(`qr-config-${target}`);
  if (panel) panel.style.display = 'block';
}

function _activateTab(target) {
  document.querySelectorAll('.qr-tab-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.qrTarget === target);
  });
  _showConfigPanel(target);
  _renderQr(target);
}

// Tab clicks
document.querySelectorAll('.qr-tab-btn').forEach(btn => {
  btn.addEventListener('click', () => _activateTab(btn.dataset.qrTarget));
});

// Open modal
document.getElementById('qr-open-btn').addEventListener('click', async () => {
  await _loadShareConfig();
  // Populate saved values
  document.getElementById('qr-wa-number').value = _qrShareConfig.whatsapp_number || '';
  document.getElementById('qr-tg-bot').value = _qrShareConfig.telegram_bot || '';
  // Show admin config if logged in
  qrAdminConfig.style.display = _isAdmin() ? 'block' : 'none';
  qrConfigResult.textContent = '';
  _activateTab(_qrActiveTarget);
  qrModal.showModal();
});

// Save WhatsApp config
document.getElementById('qr-wa-save').addEventListener('click', async () => {
  const number = document.getElementById('qr-wa-number').value.trim();
  const bot = _qrShareConfig.telegram_bot || null;
  try {
    const res = await fetch('/share/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({ whatsapp_number: number || null, telegram_bot: bot }),
    });
    if (res.ok) {
      _qrShareConfig = await res.json();
      qrConfigResult.style.cssText = 'font-size:0.8rem; margin-top:6px; color:#2e7d32;';
      qrConfigResult.textContent = 'Saved!';
      _renderQr('whatsapp');
      _showBridgeWarning('WhatsApp', 'Update the WhatsApp bridge .env file with this number, then restart the bridge — otherwise incoming messages won\'t be consumed.');
    } else {
      qrConfigResult.style.cssText = 'font-size:0.8rem; margin-top:6px; color:#b71c1c;';
      qrConfigResult.textContent = 'Save failed.';
    }
  } catch (_) {
    qrConfigResult.style.cssText = 'font-size:0.8rem; margin-top:6px; color:#b71c1c;';
    qrConfigResult.textContent = 'Save failed.';
  }
});

// Save Telegram config
document.getElementById('qr-tg-save').addEventListener('click', async () => {
  const bot = document.getElementById('qr-tg-bot').value.trim();
  const number = _qrShareConfig.whatsapp_number || null;
  try {
    const res = await fetch('/share/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({ whatsapp_number: number, telegram_bot: bot || null }),
    });
    if (res.ok) {
      _qrShareConfig = await res.json();
      qrConfigResult.style.cssText = 'font-size:0.8rem; margin-top:6px; color:#2e7d32;';
      qrConfigResult.textContent = 'Saved!';
      _renderQr('telegram');
      _showBridgeWarning('Telegram', 'Update the Telegram bridge .env file with this bot token, then restart the bridge — otherwise incoming messages won\'t be consumed.');
    } else {
      qrConfigResult.style.cssText = 'font-size:0.8rem; margin-top:6px; color:#b71c1c;';
      qrConfigResult.textContent = 'Save failed.';
    }
  } catch (_) {
    qrConfigResult.style.cssText = 'font-size:0.8rem; margin-top:6px; color:#b71c1c;';
    qrConfigResult.textContent = 'Save failed.';
  }
});

// Copy link
document.getElementById('qr-copy-btn').addEventListener('click', () => {
  if (!_qrCurrentUrl) return;
  const btn = document.getElementById('qr-copy-btn');
  navigator.clipboard.writeText(_qrCurrentUrl).then(() => {
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = 'Copy link'; }, 2000);
  });
});

// Close
document.getElementById('qr-close-btn').addEventListener('click', () => {
  qrModal.close();
});
