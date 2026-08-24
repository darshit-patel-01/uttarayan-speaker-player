// --- Playlists (admin only) ---------------------------------------------

const newPlaylistOpenBtn = document.getElementById('new-playlist-open-btn');
const newPlaylistModal = document.getElementById('new-playlist-modal');
const newPlaylistForm = document.getElementById('new-playlist-form');
const newPlaylistNameInput = document.getElementById('new-playlist-name');
const newPlaylistSaveBtn = document.getElementById('new-playlist-save-btn');
const newPlaylistCancelBtn = document.getElementById('new-playlist-cancel-btn');
const playlistsResult = document.getElementById('playlists-result');
const playlistsTable = document.getElementById('playlists-table');
const playlistsBody = document.getElementById('playlists-body');
const playlistsEmpty = document.getElementById('playlists-empty');

async function loadPlaylists() {
  if (!sessionStorage.getItem(AUTH_STORAGE_KEY)) return;

  try {
    const res = await fetch('/playlists', { headers: getAuthHeader() });
    if (res.status === 401) {
      setLoggedOut();
      return;
    }
    const data = await res.json();
    const playlists = data.playlists || [];

    playlistsBody.innerHTML = '';
    if (playlists.length === 0) {
      playlistsTable.style.display = 'none';
      playlistsEmpty.style.display = 'block';
      return;
    }
    playlistsEmpty.style.display = 'none';
    playlistsTable.style.display = 'table';

    for (const playlist of playlists) {
      const row = document.createElement('tr');

      const cell = (text) => {
        const td = document.createElement('td');
        td.textContent = text;
        return td;
      };

      row.appendChild(cell(playlist.name));
      row.appendChild(cell(playlist.song_count));

      const activeCell = document.createElement('td');
      if (playlist.is_active) {
        const badge = document.createElement('span');
        badge.textContent = 'Active';
        badge.style.color = '#1b5e20';
        badge.style.fontWeight = '600';
        activeCell.appendChild(badge);

        const deactivateBtn = document.createElement('button');
        deactivateBtn.type = 'button';
        deactivateBtn.className = 'small secondary';
        deactivateBtn.style.marginLeft = '6px';
        deactivateBtn.textContent = 'Deactivate';
        deactivateBtn.addEventListener('click', () => deactivatePlaylist(playlist.id, deactivateBtn));
        activeCell.appendChild(deactivateBtn);
      } else {
        const activateBtn = document.createElement('button');
        activateBtn.type = 'button';
        activateBtn.className = 'small secondary';
        activateBtn.textContent = 'Set active';
        activateBtn.addEventListener('click', () => activatePlaylist(playlist.id, activateBtn));
        activeCell.appendChild(activateBtn);
      }
      row.appendChild(activeCell);

      const actionCell = document.createElement('td');
      const manageBtn = document.createElement('button');
      manageBtn.type = 'button';
      manageBtn.className = 'small';
      manageBtn.textContent = 'Manage';
      manageBtn.addEventListener('click', () => openManagePlaylist(playlist.id, playlist.name));
      actionCell.appendChild(manageBtn);

      const deleteBtn = document.createElement('button');
      deleteBtn.type = 'button';
      deleteBtn.className = 'small secondary';
      deleteBtn.style.marginLeft = '6px';
      deleteBtn.textContent = 'Delete';
      deleteBtn.addEventListener('click', () => deletePlaylist(playlist.id, playlist.name, deleteBtn));
      actionCell.appendChild(deleteBtn);

      row.appendChild(actionCell);
      playlistsBody.appendChild(row);
    }
  } catch (err) {
    playlistsEmpty.textContent = 'Failed to load playlists: ' + err.message;
    playlistsEmpty.style.display = 'block';
    playlistsTable.style.display = 'none';
  }
}

async function activatePlaylist(playlistId, buttonEl) {
  buttonEl.disabled = true;
  try {
    const res = await fetch(`/playlists/${encodeURIComponent(playlistId)}/activate`, {
      method: 'POST',
      headers: getAuthHeader(),
    });
    if (res.status === 401) {
      setLoggedOut();
      return;
    }
    await loadPlaylists();
  } catch (err) {
    buttonEl.disabled = false;
  }
}

async function deactivatePlaylist(playlistId, buttonEl) {
  buttonEl.disabled = true;
  try {
    const res = await fetch(`/playlists/${encodeURIComponent(playlistId)}/deactivate`, {
      method: 'POST',
      headers: getAuthHeader(),
    });
    if (res.status === 401) {
      setLoggedOut();
      return;
    }
    await loadPlaylists();
  } catch (err) {
    buttonEl.disabled = false;
  }
}

async function deletePlaylist(playlistId, playlistName, buttonEl) {
  if (!confirm(`Delete playlist "${playlistName}"? This removes all its songs too.`)) return;
  buttonEl.disabled = true;
  try {
    const res = await fetch(`/playlists/${encodeURIComponent(playlistId)}`, {
      method: 'DELETE',
      headers: getAuthHeader(),
    });
    if (res.status === 401) {
      setLoggedOut();
      return;
    }
    await loadPlaylists();
  } catch (err) {
    buttonEl.disabled = false;
  }
}

newPlaylistOpenBtn.addEventListener('click', () => {
  playlistsResult.style.display = 'none';
  newPlaylistNameInput.value = '';
  newPlaylistModal.showModal();
  newPlaylistNameInput.focus();
});

newPlaylistCancelBtn.addEventListener('click', () => {
  newPlaylistModal.close();
});

newPlaylistForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const name = newPlaylistNameInput.value.trim();
  if (!name) return;

  newPlaylistSaveBtn.disabled = true;
  try {
    const res = await fetch('/playlists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({ name }),
    });
    if (res.status === 401) {
      setLoggedOut();
      return;
    }
    const data = await res.json();

    if (!res.ok) {
      playlistsResult.className = 'err';
      playlistsResult.textContent = formatError(data);
      playlistsResult.style.display = 'block';
    } else {
      newPlaylistModal.close();
      loadPlaylists();
    }
  } catch (err) {
    playlistsResult.className = 'err';
    playlistsResult.textContent = 'Request failed: ' + err.message;
    playlistsResult.style.display = 'block';
  } finally {
    newPlaylistSaveBtn.disabled = false;
  }
});

// --- Manage playlist songs (admin only) ---------------------------------

const managePlaylistModal = document.getElementById('manage-playlist-modal');
const managePlaylistTitle = document.getElementById('manage-playlist-title');
const managePlaylistAddForm = document.getElementById('manage-playlist-add-form');
const managePlaylistUrlInput = document.getElementById('manage-playlist-url');
const managePlaylistAddBtn = document.getElementById('manage-playlist-add-btn');
const managePlaylistResult = document.getElementById('manage-playlist-result');
const managePlaylistTable = document.getElementById('manage-playlist-table');
const managePlaylistBody = document.getElementById('manage-playlist-body');
const managePlaylistEmpty = document.getElementById('manage-playlist-empty');
const managePlaylistCloseBtn = document.getElementById('manage-playlist-close-btn');

let currentManagedPlaylistId = null;
let managedSongIds = [];
let dragSrcIndex = null;

function openManagePlaylist(playlistId, playlistName) {
  currentManagedPlaylistId = playlistId;
  managePlaylistTitle.textContent = `Manage playlist: ${playlistName}`;
  managePlaylistResult.style.display = 'none';
  managePlaylistUrlInput.value = '';
  managePlaylistModal.showModal();
  loadManagedPlaylistSongs();
}

async function sendReorder(newOrder) {
  try {
    const res = await fetch(`/playlists/${encodeURIComponent(currentManagedPlaylistId)}/songs/reorder`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({ song_ids: newOrder }),
    });
    if (res.status === 401) { setLoggedOut(); return; }
    if (res.ok) await loadManagedPlaylistSongs();
  } catch (_) {}
}

async function loadManagedPlaylistSongs() {
  if (!currentManagedPlaylistId) return;
  try {
    const res = await fetch(`/playlists/${encodeURIComponent(currentManagedPlaylistId)}/songs`, {
      headers: getAuthHeader(),
    });
    if (res.status === 401) {
      setLoggedOut();
      return;
    }
    const data = await res.json();
    const songs = data.songs || [];

    managedSongIds = songs.map(s => s.id);
    managePlaylistBody.innerHTML = '';

    if (songs.length === 0) {
      managePlaylistTable.style.display = 'none';
      managePlaylistEmpty.style.display = 'block';
      return;
    }
    managePlaylistEmpty.style.display = 'none';
    managePlaylistTable.style.display = 'table';

    songs.forEach((song, index) => {
      const row = document.createElement('tr');
      row.dataset.index = index;

      const cell = (text, className) => {
        const td = document.createElement('td');
        td.textContent = text;
        if (className) td.className = className;
        return td;
      };

      const handleCell = document.createElement('td');
      handleCell.className = 'drag-handle';
      handleCell.textContent = '⠿';
      handleCell.title = 'Drag to reorder';
      row.appendChild(handleCell);

      const titleCell = document.createElement('td');
      titleCell.className = 'title-cell';
      titleCell.style.lineHeight = '1.3';
      const titleLink = document.createElement('a');
      titleLink.href = song.url;
      titleLink.target = '_blank';
      titleLink.rel = 'noopener noreferrer';
      titleLink.textContent = song.title || song.url;
      titleCell.appendChild(titleLink);
      if (song.uploader) {
        const sub = document.createElement('div');
        sub.style.cssText = 'font-size:0.75rem;color:#888;margin-top:1px;';
        sub.textContent = song.uploader;
        titleCell.appendChild(sub);
      }
      row.appendChild(titleCell);

      row.appendChild(cell(song.duration));

      const actionCell = document.createElement('td');
      const removeBtn = document.createElement('button');
      removeBtn.type = 'button';
      removeBtn.className = 'small secondary';
      removeBtn.textContent = 'Remove';
      removeBtn.addEventListener('click', () => removeManagedPlaylistSong(song.id, removeBtn));
      actionCell.appendChild(removeBtn);

      const plEnqBtn = document.createElement('button');
      plEnqBtn.type = 'button';
      plEnqBtn.className = 'small';
      plEnqBtn.style.marginLeft = '6px';
      plEnqBtn.textContent = 'Enqueue';
      const plEnqResult = document.createElement('div');
      plEnqResult.className = 'history-enqueue-result';
      plEnqBtn.addEventListener('click', async () => {
        plEnqBtn.disabled = true;
        plEnqResult.className = 'history-enqueue-result';
        plEnqResult.textContent = 'Enqueueing…';
        try {
          const r = await fetch('/enqueue', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
            body: JSON.stringify({ urls: [song.url] }),
          });
          const d = await r.json();
          if (!r.ok) {
            plEnqResult.className = 'history-enqueue-result err';
            plEnqResult.textContent = formatError(d);
          } else if (d.enqueued && d.enqueued.length > 0) {
            const s = d.enqueued[0];
            plEnqResult.className = 'history-enqueue-result ok';
            plEnqResult.textContent = `✓ Position ${s.position_in_queue}, wait ${s.estimated_wait}`;
            loadNowPlaying();
            loadWaitTime();
          } else {
            plEnqResult.className = 'history-enqueue-result err';
            plEnqResult.textContent = d.rejected?.[0]?.reason || 'Rejected.';
          }
        } catch (err) {
          plEnqResult.className = 'history-enqueue-result err';
          plEnqResult.textContent = 'Request failed.';
        } finally {
          plEnqBtn.disabled = false;
        }
      });
      actionCell.appendChild(plEnqBtn);
      actionCell.appendChild(plEnqResult);
      row.appendChild(actionCell);

      row.draggable = true;

      row.addEventListener('dragstart', (e) => {
        dragSrcIndex = index;
        e.dataTransfer.effectAllowed = 'move';
        setTimeout(() => row.classList.add('dragging'), 0);
      });

      row.addEventListener('dragend', () => {
        row.classList.remove('dragging');
        managePlaylistBody.querySelectorAll('tr').forEach(r => r.classList.remove('drag-over'));
      });

      row.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = 'move';
        managePlaylistBody.querySelectorAll('tr').forEach((r, i) => {
          r.classList.toggle('drag-over', i === index);
        });
      });

      row.addEventListener('drop', (e) => {
        e.preventDefault();
        managePlaylistBody.querySelectorAll('tr').forEach(r => r.classList.remove('drag-over'));
        if (dragSrcIndex === null || dragSrcIndex === index) return;
        const newOrder = [...managedSongIds];
        const [moved] = newOrder.splice(dragSrcIndex, 1);
        newOrder.splice(index, 0, moved);
        dragSrcIndex = null;
        sendReorder(newOrder);
      });

      managePlaylistBody.appendChild(row);
    });
  } catch (err) {
    managePlaylistEmpty.textContent = 'Failed to load songs: ' + err.message;
    managePlaylistEmpty.style.display = 'block';
    managePlaylistTable.style.display = 'none';
  }
}

async function removeManagedPlaylistSong(songId, buttonEl) {
  if (!currentManagedPlaylistId) return;
  buttonEl.disabled = true;
  try {
    const res = await fetch(
      `/playlists/${encodeURIComponent(currentManagedPlaylistId)}/songs/${encodeURIComponent(songId)}`,
      { method: 'DELETE', headers: getAuthHeader() }
    );
    if (res.status === 401) {
      setLoggedOut();
      return;
    }
    await loadManagedPlaylistSongs();
    loadPlaylists();
  } catch (err) {
    buttonEl.disabled = false;
  }
}

managePlaylistAddForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  if (!currentManagedPlaylistId) return;
  const url = managePlaylistUrlInput.value.trim();
  if (!url) return;

  managePlaylistAddBtn.disabled = true;
  managePlaylistResult.className = '';
  managePlaylistResult.textContent = 'Adding...';
  managePlaylistResult.style.display = 'block';

  try {
    const res = await fetch(`/playlists/${encodeURIComponent(currentManagedPlaylistId)}/songs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeader() },
      body: JSON.stringify({ urls: [url] }),
    });
    if (res.status === 401) {
      setLoggedOut();
      return;
    }
    const data = await res.json();

    if (!res.ok) {
      managePlaylistResult.className = 'err';
      managePlaylistResult.textContent = formatError(data);
    } else if (data.added.length > 0) {
      managePlaylistResult.style.display = 'none';
      managePlaylistUrlInput.value = '';
      loadManagedPlaylistSongs();
      loadPlaylists();
    } else {
      const rejection = data.rejected[0];
      managePlaylistResult.className = 'err';
      managePlaylistResult.textContent = rejection ? rejection.reason : 'Rejected.';
    }
  } catch (err) {
    managePlaylistResult.className = 'err';
    managePlaylistResult.textContent = 'Request failed: ' + err.message;
  } finally {
    managePlaylistAddBtn.disabled = false;
  }
});

managePlaylistCloseBtn.addEventListener('click', () => {
  managePlaylistModal.close();
  currentManagedPlaylistId = null;
});
