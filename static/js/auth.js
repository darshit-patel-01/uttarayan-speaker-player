// --- Auth -------------------------------------------------------------

const AUTH_STORAGE_KEY = 'ytplayer_admin_auth';

function getAuthHeader() {
  const token = sessionStorage.getItem(AUTH_STORAGE_KEY);
  return token ? { Authorization: 'Basic ' + token } : {};
}

const profileWidget = document.getElementById('profile-widget');
const profileIconText = document.getElementById('profile-icon-text');

function setLoggedIn(username) {
  document.getElementById('login-form').style.display = 'none';
  const bar = document.getElementById('logged-in-bar');
  bar.style.display = 'flex';
  document.getElementById('logged-in-user').textContent = username;
  document.getElementById('tab-btn-dashboard').classList.remove('admin-only');
  document.getElementById('tab-btn-queue').classList.remove('admin-only');
  document.getElementById('tab-btn-playlists').classList.remove('admin-only');
  document.getElementById('tab-btn-blacklist').classList.remove('admin-only');
  document.getElementById('tab-btn-messages').classList.remove('admin-only');
  document.getElementById('tab-btn-settings').classList.remove('admin-only');
  profileWidget.classList.add('logged-in');
  profileIconText.textContent = username.charAt(0).toUpperCase();
  document.getElementById('np-admin-controls').style.display = 'block';
  loadVolume();
  loadQueue();
  loadPlaylists();
  loadBlacklist();
  loadMessages();
  loadDashboard();
  loadSettings();
}

function setLoggedOut() {
  sessionStorage.removeItem(AUTH_STORAGE_KEY);
  document.getElementById('login-form').style.display = 'flex';
  document.getElementById('logged-in-bar').style.display = 'none';
  const dashboardTabBtn = document.getElementById('tab-btn-dashboard');
  const queueTabBtn = document.getElementById('tab-btn-queue');
  const playlistsTabBtn = document.getElementById('tab-btn-playlists');
  const blacklistTabBtn = document.getElementById('tab-btn-blacklist');
  const messagesTabBtn = document.getElementById('tab-btn-messages');
  const settingsTabBtn = document.getElementById('tab-btn-settings');
  const wasOnAdminTab = dashboardTabBtn.classList.contains('active')
    || queueTabBtn.classList.contains('active')
    || playlistsTabBtn.classList.contains('active')
    || blacklistTabBtn.classList.contains('active')
    || messagesTabBtn.classList.contains('active')
    || settingsTabBtn.classList.contains('active');
  dashboardTabBtn.classList.add('admin-only');
  queueTabBtn.classList.add('admin-only');
  playlistsTabBtn.classList.add('admin-only');
  blacklistTabBtn.classList.add('admin-only');
  messagesTabBtn.classList.add('admin-only');
  settingsTabBtn.classList.add('admin-only');
  if (wasOnAdminTab) switchTab('enqueue');
  profileWidget.classList.remove('logged-in');
  profileIconText.textContent = '🪁';
  document.getElementById('np-admin-controls').style.display = 'none';
}

document.getElementById('profile-icon').addEventListener('click', (e) => {
  e.stopPropagation();
  profileWidget.classList.toggle('open');
});
document.addEventListener('click', (e) => {
  if (!profileWidget.contains(e.target)) {
    profileWidget.classList.remove('open');
  }
});

const loginForm = document.getElementById('login-form');
const loginResult = document.getElementById('login-result');

loginForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const username = document.getElementById('login-username').value.trim();
  const password = document.getElementById('login-password').value;
  if (!username || !password) return;

  const token = btoa(`${username}:${password}`);
  const loginBtn = document.getElementById('login-btn');
  loginBtn.disabled = true;
  loginResult.className = '';
  loginResult.textContent = 'Logging in...';
  loginResult.style.display = 'block';

  try {
    const res = await fetch('/login', {
      method: 'POST',
      headers: { Authorization: 'Basic ' + token },
    });
    const data = await res.json();

    if (!res.ok) {
      loginResult.className = 'err';
      loginResult.textContent = formatError(data);
    } else {
      sessionStorage.setItem(AUTH_STORAGE_KEY, token);
      loginResult.style.display = 'none';
      document.getElementById('login-password').value = '';
      setLoggedIn(data.username);
    }
  } catch (err) {
    loginResult.className = 'err';
    loginResult.textContent = 'Request failed: ' + err.message;
  } finally {
    loginBtn.disabled = false;
  }
});

document.getElementById('logout-btn').addEventListener('click', () => {
  setLoggedOut();
});

(async () => {
  const token = sessionStorage.getItem(AUTH_STORAGE_KEY);
  if (!token) {
    setLoggedOut();
    return;
  }
  try {
    const res = await fetch('/login', {
      method: 'POST',
      headers: { Authorization: 'Basic ' + token },
    });
    if (res.ok) {
      const data = await res.json();
      setLoggedIn(data.username);
    } else {
      setLoggedOut();
    }
  } catch {
    setLoggedOut();
  }
})();
