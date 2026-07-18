// --- Settings (admin only) ----------------------------------------------

const settingsFields = document.getElementById('settings-fields');
const settingsResult = document.getElementById('settings-result');
const settingsForm = document.getElementById('settings-form');
const settingsResetBtn = document.getElementById('settings-reset-btn');

function showSettingsMessage(text, isError) {
  settingsResult.className = isError ? 'err' : 'ok';
  settingsResult.textContent = text;
  settingsResult.style.display = 'block';
}

function renderSettings(settings) {
  settingsFields.innerHTML = '';
  const order = [
    'rate_limit_max_songs', 'rate_limit_window_seconds', 'max_queue_wait_seconds',
    'max_duration_seconds', 'normalize_volume', 'loudnorm_target_lufs', 'crossfade_lead_seconds',
  ];
  const keys = order.filter(k => k in settings).concat(Object.keys(settings).filter(k => !order.includes(k)));

  for (const key of keys) {
    const s = settings[key];
    const row = document.createElement('div');
    row.className = 'setting-row';

    const info = document.createElement('div');
    info.className = 'setting-info';
    const label = document.createElement('div');
    label.className = 'setting-label';
    label.textContent = s.label || key;
    const def = document.createElement('span');
    def.className = 'setting-default';
    def.textContent = `default: ${s.default}`;
    label.appendChild(def);
    info.appendChild(label);
    if (s.help) {
      const help = document.createElement('div');
      help.className = 'setting-help';
      help.textContent = s.help;
      info.appendChild(help);
    }

    const control = document.createElement('div');
    control.className = 'setting-control';
    let input;
    if (s.type === 'bool') {
      input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = !!s.value;
    } else {
      input = document.createElement('input');
      input.type = 'number';
      input.value = s.value;
      if (s.type === 'float') input.step = 'any';
      if (typeof s.min === 'number') input.min = s.min;
      if (typeof s.max === 'number') input.max = s.max;
    }
    input.dataset.key = key;
    input.dataset.type = s.type;
    control.appendChild(input);
    if (s.unit) {
      const unit = document.createElement('span');
      unit.className = 'setting-unit';
      unit.textContent = s.unit;
      control.appendChild(unit);
    }

    row.appendChild(info);
    row.appendChild(control);
    settingsFields.appendChild(row);
  }
}

async function loadSettings() {
  if (!sessionStorage.getItem(AUTH_STORAGE_KEY)) return;
  try {
    const res = await fetch('/config', { headers: getAuthHeader() });
    if (res.status === 401) { setLoggedOut(); return; }
    const data = await res.json();
    renderSettings(data.settings || {});
  } catch (err) {
    showSettingsMessage('Failed to load settings: ' + err.message, true);
  }
}

settingsForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const changes = {};
  settingsFields.querySelectorAll('input[data-key]').forEach(input => {
    const key = input.dataset.key;
    if (input.dataset.type === 'bool') {
      changes[key] = input.checked;
    } else {
      changes[key] = input.value === '' ? null : Number(input.value);
    }
  });
  Object.keys(changes).forEach(k => { if (changes[k] === null) delete changes[k]; });
  try {
    const res = await fetch('/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({ changes }),
    });
    if (res.status === 401) { setLoggedOut(); return; }
    const data = await res.json();
    if (!res.ok) { showSettingsMessage(formatError(data), true); return; }
    renderSettings(data.settings || {});
    showSettingsMessage('Settings saved.', false);
  } catch (err) {
    showSettingsMessage('Failed to save: ' + err.message, true);
  }
});

settingsResetBtn.addEventListener('click', async () => {
  if (!confirm('Reset all settings to their .env defaults?')) return;
  try {
    const res = await fetch('/config/reset', { method: 'POST', headers: getAuthHeader() });
    if (res.status === 401) { setLoggedOut(); return; }
    const data = await res.json();
    renderSettings(data.settings || {});
    showSettingsMessage('Settings reset to defaults.', false);
  } catch (err) {
    showSettingsMessage('Failed to reset: ' + err.message, true);
  }
});
