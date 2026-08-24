// --- Messages (admin only) -----------------------------------------------

const MESSAGES_SOURCE_LABELS = { whatsapp: 'WhatsApp', telegram: 'Telegram', ip: 'Web (IP)', web: 'Web' };

async function loadMessages() {
  if (!sessionStorage.getItem(AUTH_STORAGE_KEY)) return;
  const listEl = document.getElementById('messages-list');
  const emptyEl = document.getElementById('messages-empty');
  const badge = document.getElementById('messages-badge');

  try {
    const res = await fetch('/messages', { headers: getAuthHeader() });
    if (res.status === 401) { setLoggedOut(); return; }
    const data = await res.json();
    const msgs = (data.messages || []).slice().reverse();

    listEl.innerHTML = '';
    const unread = msgs.filter(m => !m.read).length;
    if (unread > 0) {
      badge.textContent = unread;
      badge.style.display = 'inline';
    } else {
      badge.style.display = 'none';
    }

    if (msgs.length === 0) {
      emptyEl.style.display = 'block';
      return;
    }
    emptyEl.style.display = 'none';

    for (const msg of msgs) {
      const card = document.createElement('div');
      card.style.cssText = `padding:12px 14px; border-radius:8px; margin-bottom:10px; border:1px solid ${msg.read ? '#e0e0e0' : '#ffcdd2'}; background:${msg.read ? '#fafafa' : '#fff5f5'};`;

      const header = document.createElement('div');
      header.style.cssText = 'display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; flex-wrap:wrap; gap:6px;';

      const from = document.createElement('span');
      from.style.cssText = 'font-weight:600; font-size:0.9rem;';
      from.textContent = `${MESSAGES_SOURCE_LABELS[msg.source] || msg.source} — ${msg.requester_id}`;
      header.appendChild(from);

      const time = document.createElement('span');
      time.style.cssText = 'font-size:0.78rem; color:#888;';
      const d = new Date(msg.timestamp * 1000);
      time.textContent = d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
      header.appendChild(time);
      card.appendChild(header);

      const body = document.createElement('p');
      body.style.cssText = 'margin:0 0 8px; font-size:0.9rem; line-height:1.4; white-space:pre-wrap; word-break:break-word;';
      body.textContent = msg.text;
      card.appendChild(body);

      const actions = document.createElement('div');
      actions.style.cssText = 'display:flex; gap:6px; align-items:center; flex-wrap:wrap;';

      const unblockBtn = document.createElement('button');
      unblockBtn.type = 'button';
      unblockBtn.className = 'small';
      unblockBtn.style.background = 'var(--kite-green)';
      unblockBtn.textContent = '🔓 Unblock';
      unblockBtn.addEventListener('click', async () => {
        unblockBtn.disabled = true;
        unblockBtn.textContent = 'Unblocking…';
        const blSource = msg.source === 'web' ? 'ip' : msg.source;
        const params = new URLSearchParams({ source: blSource, value: msg.requester_id });
        try {
          const r = await fetch(`/blacklist/requester?${params.toString()}`, { method: 'DELETE', headers: getAuthHeader() });
          if (r.ok) {
            unblockBtn.textContent = '✓ Unblocked';
            unblockBtn.style.background = '#aaa';
          } else if (r.status === 404) {
            unblockBtn.textContent = 'Not blocked';
            unblockBtn.style.background = '#aaa';
          } else {
            unblockBtn.textContent = 'Failed';
            unblockBtn.disabled = false;
          }
        } catch (_) {
          unblockBtn.textContent = 'Failed';
          unblockBtn.disabled = false;
        }
      });
      actions.appendChild(unblockBtn);

      if (!msg.read) {
        const readBtn = document.createElement('button');
        readBtn.type = 'button';
        readBtn.className = 'small secondary';
        readBtn.textContent = 'Mark read';
        readBtn.addEventListener('click', async () => {
          readBtn.disabled = true;
          await fetch(`/messages/${msg.id}/read`, { method: 'POST', headers: getAuthHeader() });
          loadMessages();
        });
        actions.appendChild(readBtn);
      }

      const replyBtn = document.createElement('button');
      replyBtn.type = 'button';
      replyBtn.className = 'small';
      replyBtn.style.background = 'var(--kite-green)';
      replyBtn.textContent = '↩ Reply';
      const replyPanel = document.createElement('div');
      replyPanel.style.cssText = 'display:none; margin-top:8px; padding:10px 12px; background:#f0faf8; border:1px solid #b2dfdb; border-radius:6px;';
      replyBtn.addEventListener('click', () => {
        replyPanel.style.display = replyPanel.style.display === 'none' ? 'block' : 'none';
        if (replyPanel.style.display === 'block') replyPanel.querySelector('textarea').focus();
      });
      actions.appendChild(replyBtn);

      const delBtn = document.createElement('button');
      delBtn.type = 'button';
      delBtn.className = 'small';
      delBtn.style.background = '#c62828';
      delBtn.textContent = 'Delete';
      delBtn.addEventListener('click', async () => {
        delBtn.disabled = true;
        await fetch(`/messages/${msg.id}`, { method: 'DELETE', headers: getAuthHeader() });
        loadMessages();
      });
      actions.appendChild(delBtn);

      card.appendChild(actions);

      const replyTextarea = document.createElement('textarea');
      replyTextarea.style.cssText = 'width:100%; min-height:60px; padding:8px; border:1px solid #ccc; border-radius:4px; font-size:0.9rem; resize:vertical; box-sizing:border-box;';
      replyTextarea.maxLength = 400;
      replyTextarea.placeholder = 'Type your reply (max 400 characters)…';
      replyPanel.appendChild(replyTextarea);

      const replyCharCount = document.createElement('div');
      replyCharCount.style.cssText = 'font-size:0.75rem; color:#888; text-align:right; margin-top:2px;';
      replyCharCount.textContent = '0 / 400';
      replyTextarea.addEventListener('input', () => {
        replyCharCount.textContent = `${replyTextarea.value.length} / 400`;
      });
      replyPanel.appendChild(replyCharCount);

      const replyActions = document.createElement('div');
      replyActions.style.cssText = 'display:flex; gap:6px; margin-top:6px;';
      const sendBtn = document.createElement('button');
      sendBtn.type = 'button';
      sendBtn.className = 'small';
      sendBtn.style.background = 'var(--kite-green)';
      sendBtn.textContent = 'Send';
      const replyFeedback = document.createElement('span');
      replyFeedback.style.cssText = 'font-size:0.8rem; align-self:center;';

      sendBtn.addEventListener('click', async () => {
        const replyText = replyTextarea.value.trim();
        if (!replyText) { replyFeedback.style.color = '#b71c1c'; replyFeedback.textContent = 'Cannot be empty.'; return; }
        sendBtn.disabled = true;
        replyFeedback.style.color = '#555';
        replyFeedback.textContent = 'Sending…';
        try {
          const r = await fetch('/messages/reply', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
            body: JSON.stringify({ source: msg.source, requester_id: msg.requester_id, text: replyText }),
          });
          if (r.ok) {
            replyFeedback.style.color = '#1b5e20';
            replyFeedback.textContent = '✓ Reply sent! Will be delivered shortly.';
            replyTextarea.value = '';
            replyCharCount.textContent = '0 / 400';
            if (!msg.read) {
              await fetch(`/messages/${msg.id}/read`, { method: 'POST', headers: getAuthHeader() });
            }
            setTimeout(loadMessages, 1500);
          } else {
            const d = await r.json();
            replyFeedback.style.color = '#b71c1c';
            replyFeedback.textContent = formatError(d);
          }
        } catch (_) {
          replyFeedback.style.color = '#b71c1c';
          replyFeedback.textContent = 'Failed to send.';
        } finally {
          sendBtn.disabled = false;
        }
      });

      const cancelReplyBtn = document.createElement('button');
      cancelReplyBtn.type = 'button';
      cancelReplyBtn.className = 'small secondary';
      cancelReplyBtn.textContent = 'Cancel';
      cancelReplyBtn.addEventListener('click', () => { replyPanel.style.display = 'none'; });

      replyActions.appendChild(sendBtn);
      replyActions.appendChild(cancelReplyBtn);
      replyActions.appendChild(replyFeedback);
      replyPanel.appendChild(replyActions);
      card.appendChild(replyPanel);

      listEl.appendChild(card);
    }
  } catch (err) {
    listEl.innerHTML = '';
    emptyEl.textContent = 'Failed to load messages.';
    emptyEl.style.display = 'block';
  }
}

document.getElementById('refresh-messages-btn').addEventListener('click', loadMessages);
document.querySelectorAll('.tab-btn').forEach(btn => {
  if (btn.dataset.tab === 'messages') btn.addEventListener('click', loadMessages);
});
