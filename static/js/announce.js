// --- Announce (admin only) ------------------------------------------------
// Two ways to cut into the music: type a message (spoken via TTS) or record
// one with the mic. Both POST /announce — the same endpoint the WhatsApp
// bridge uses — so the player pauses the song, speaks "Admin announcement",
// plays this, and resumes.

const announceTextForm = document.getElementById('announce-text-form');
const announceText = document.getElementById('announce-text');
const announceTextCount = document.getElementById('announce-text-count');
const announceTextBtn = document.getElementById('announce-text-btn');
const announceTextRepeat = document.getElementById('announce-text-repeat');

const recBtn = document.getElementById('announce-rec-btn');
const recStatus = document.getElementById('announce-rec-status');
const recPreview = document.getElementById('announce-rec-preview');
const recAudio = document.getElementById('announce-rec-audio');
const recDiscard = document.getElementById('announce-rec-discard');
const recSend = document.getElementById('announce-rec-send');
const recRepeat = document.getElementById('announce-rec-repeat');
const recUnsupported = document.getElementById('announce-rec-unsupported');

const REC_MAX_SECONDS = 120;

// --- Text -----------------------------------------------------------------

announceText.addEventListener('input', () => {
  announceTextCount.textContent = `${announceText.value.length} / 300`;
});

announceTextForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = announceText.value.trim();
  if (!text) return;
  const repeat = parseInt(announceTextRepeat.value, 10) || 1;

  announceTextBtn.disabled = true;
  const original = announceTextBtn.textContent;
  announceTextBtn.textContent = 'Sending…';
  try {
    const res = await fetch('/announce', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({ text, repeat }),
    });
    if (res.status === 401) { setLoggedOut(); return; }
    const data = await res.json();
    if (!res.ok) {
      showToast(formatError(data), 'error', 5000);
      return;
    }
    showToast(`Announcing now${repeat > 1 ? ` ×${repeat}` : ''}`, 'success');
    announceText.value = '';
    announceTextCount.textContent = '0 / 300';
  } catch (err) {
    showToast(err instanceof SyntaxError ? 'Could not reach the server — is it running?' : 'Request failed: ' + err.message, 'error', 5000);
  } finally {
    announceTextBtn.disabled = false;
    announceTextBtn.textContent = original;
  }
});

// --- Record ---------------------------------------------------------------

let recorder = null;
let recChunks = [];
let recBlob = null;
let recMime = '';
let recTimer = null;
let recStartedAt = 0;
let recStream = null;

function _pickMime() {
  // Prefer Opus in WebM (Chrome/Firefox/Edge); Safari only does MP4/AAC.
  // Both are in the server's allow-list and ffmpeg decodes both.
  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'];
  return candidates.find(m => window.MediaRecorder && MediaRecorder.isTypeSupported(m)) || '';
}

function _recSupported() {
  return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder && _pickMime());
}

if (!_recSupported()) {
  recBtn.disabled = true;
  recUnsupported.style.display = 'block';
}

function _fmtSecs(s) {
  s = Math.floor(s);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function _stopStream() {
  if (recStream) {
    recStream.getTracks().forEach(t => t.stop());
    recStream = null;
  }
}

async function startRecording() {
  try {
    recStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    showToast(err.name === 'NotAllowedError'
      ? 'Microphone access was blocked — allow it in the browser and try again.'
      : 'Could not open the microphone: ' + err.message, 'error', 5000);
    return;
  }
  recMime = _pickMime();
  recChunks = [];
  recBlob = null;
  recorder = new MediaRecorder(recStream, { mimeType: recMime });
  recorder.addEventListener('dataavailable', (e) => { if (e.data.size) recChunks.push(e.data); });
  recorder.addEventListener('stop', () => {
    _stopStream();
    clearInterval(recTimer);
    recBtn.classList.remove('recording');
    recBtn.textContent = '● Record again';
    if (!recChunks.length) {
      recStatus.textContent = 'Nothing was recorded.';
      return;
    }
    recBlob = new Blob(recChunks, { type: recMime.split(';')[0] });
    recAudio.src = URL.createObjectURL(recBlob);
    recPreview.style.display = 'block';
    recStatus.textContent = `Recorded ${_fmtSecs((Date.now() - recStartedAt) / 1000)} — listen back, then announce or discard.`;
  });

  recorder.start();
  recStartedAt = Date.now();
  recPreview.style.display = 'none';
  recBtn.classList.add('recording');
  recBtn.textContent = '■ Stop';
  recTimer = setInterval(() => {
    const elapsed = (Date.now() - recStartedAt) / 1000;
    recStatus.textContent = `Recording… ${_fmtSecs(elapsed)}`;
    if (elapsed >= REC_MAX_SECONDS) stopRecording();
  }, 250);
}

function stopRecording() {
  if (recorder && recorder.state !== 'inactive') recorder.stop();
}

recBtn.addEventListener('click', () => {
  if (recorder && recorder.state === 'recording') stopRecording();
  else startRecording();
});

recDiscard.addEventListener('click', () => {
  recBlob = null;
  recChunks = [];
  if (recAudio.src) URL.revokeObjectURL(recAudio.src);
  recAudio.removeAttribute('src');
  recPreview.style.display = 'none';
  recBtn.textContent = '● Start recording';
  recStatus.textContent = 'Uses your microphone. Max 2 minutes.';
});

recSend.addEventListener('click', async () => {
  if (!recBlob) return;
  const repeat = parseInt(recRepeat.value, 10) || 1;
  recSend.disabled = true;
  const original = recSend.textContent;
  recSend.textContent = 'Sending…';
  try {
    const res = await fetch('/announce', {
      method: 'POST',
      headers: { 'Content-Type': recBlob.type, 'X-Repeat': String(repeat), ...getAuthHeader() },
      body: recBlob,
    });
    if (res.status === 401) { setLoggedOut(); return; }
    const data = await res.json();
    if (!res.ok) {
      showToast(formatError(data), 'error', 5000);
      return;
    }
    showToast(`Announcing your recording${repeat > 1 ? ` ×${repeat}` : ''}`, 'success');
    recDiscard.click();
  } catch (err) {
    showToast(err instanceof SyntaxError ? 'Could not reach the server — is it running?' : 'Request failed: ' + err.message, 'error', 5000);
  } finally {
    recSend.disabled = false;
    recSend.textContent = original;
  }
});

// Leaving the tab mid-recording shouldn't leave the mic open.
registerTabRefresh('announce', () => {});
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    if (btn.dataset.tab !== 'announce' && recorder && recorder.state === 'recording') stopRecording();
  });
});
