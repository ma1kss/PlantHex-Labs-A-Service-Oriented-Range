import { hasAnyRole } from './state.js';
import { events as eventsApi } from './http.js';
import { el, escapeHtml } from './dom.js';
import { modalConfirm, modalAlert } from './modal.js';
import { markAllSeen, subscribe } from './notifications.js';

const EDITOR_ROLES = ['admin', 'instructor'];
const isEditor = () => hasAnyRole(...EDITOR_ROLES);

export async function renderAnnouncements(outlet) {
  outlet.appendChild(el('div', { class: 'page-head' },
    el('h1', {}, 'Announcements'),
    el('p',  {}, isEditor()
      ? 'Publish updates the whole platform sees in real time.'
      : 'Updates from your instructors and the SOR admins.'),
  ));

  if (isEditor()) outlet.appendChild(buildComposer());

  const listHost = el('div', { id: 'announcements-list' });
  outlet.appendChild(listHost);
  await refresh(listHost);

  // Mark everything seen the moment the tab is opened.
  markAllSeen();

  // Hot-update when a fresh announcement arrives.
  const off = subscribe((env) => {
    if (env.type === 'event' && (env.routing_key || '').startsWith('announcement.')) {
      refresh(listHost);
    }
  });
  // The router blows away outlet on the next route(); make sure we unsubscribe.
  outlet.dataset.unsubAnnouncements = '1';
  outlet.addEventListener('sor:teardown', off, { once: true });
}

// Composer (editors only)

function buildComposer() {
  const panel = el('div', { class: 'panel', style: 'margin-bottom:1rem;' });
  panel.innerHTML = `
    <h3>New announcement</h3>
    <form id="ann-form" autocomplete="off">
      <div class="ch-form-grid">
        <div class="field span-2"><label>Title</label>
          <input name="title" required maxlength="255" placeholder="Heads up…" /></div>
        <div class="field span-2"><label>Body</label>
          <textarea name="body" rows="3" placeholder="Optional context."></textarea></div>
      </div>
      <div style="display:flex;gap:.6rem;align-items:center;margin-top:.5rem;">
        <button class="btn" type="submit" id="ann-btn">Publish</button>
        <span id="ann-msg" class="msg" style="margin:0;"></span>
      </div>
    </form>
  `;

  panel.querySelector('#ann-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const form = ev.currentTarget;
    const msg  = panel.querySelector('#ann-msg');
    const btn  = panel.querySelector('#ann-btn');
    msg.textContent = ''; msg.classList.remove('show', 'error', 'success');

    const title = form.elements.title.value.trim();
    const body  = form.elements.body.value;
    if (!title) {
      msg.classList.add('show', 'error'); msg.textContent = 'Title is required.';
      return;
    }

    btn.disabled = true; btn.textContent = 'Publishing…';
    try {
      await eventsApi('POST', '/announcements', { auth: true, body: { title, body } });
      msg.classList.add('show', 'success'); msg.textContent = 'Announcement published.';
      form.reset();
      await refresh(document.getElementById('announcements-list'));
    } catch (err) {
      msg.classList.add('show', 'error'); msg.textContent = err.message || 'Publish failed.';
    } finally {
      btn.disabled = false; btn.textContent = 'Publish';
    }
  });

  return panel;
}

// List

async function refresh(host) {
  if (!host) return;
  host.innerHTML = '<div class="empty"><span class="spinner"></span> loading…</div>';

  let list;
  try {
    list = await eventsApi('GET', '/announcements', { auth: true });
  } catch (err) {
    host.innerHTML = `<div class="empty">Failed to load: ${escapeHtml(err.message)}</div>`;
    return;
  }

  if (!list.length) {
    host.innerHTML = '<div class="empty">No announcements yet.</div>';
    return;
  }

  host.innerHTML = '';
  list.forEach((a) => host.appendChild(buildCard(a, host)));
  // Refresh the "seen" timestamp now that everything is on screen.
  markAllSeen();
}

function buildCard(a, host) {
  const card = el('div', { class: 'panel announcement-card' });
  const when = new Date(a.created_at);
  card.innerHTML = `
    <div class="row" style="align-items:flex-start;">
      <h3 style="margin:0;">${escapeHtml(a.title)}</h3>
      <span class="badge ${a.author_role === 'admin' ? 'role-admin' : 'role-instructor'}">
        ${escapeHtml(a.author_role || 'staff')}
      </span>
    </div>
    <div class="hint mono" style="font-size:.72rem;margin:.2rem 0 .6rem;">
      ${escapeHtml(a.author_name || 'staff')} · ${escapeHtml(when.toLocaleString())}
      ${a.updated_at && a.updated_at !== a.created_at
          ? ` · edited ${escapeHtml(new Date(a.updated_at).toLocaleString())}`
          : ''}
    </div>
    <div class="announcement-body">${escapeHtml(a.body || '')}</div>
  `;

  if (isEditor()) {
    const actions = el('div', { style: 'display:flex;gap:.5rem;margin-top:.6rem;' });
    const editBtn = el('button', { class: 'btn btn-ghost btn-sm', type: 'button' }, 'Edit');
    editBtn.addEventListener('click', () => openEditModal(a, host));
    actions.appendChild(editBtn);
    const delBtn = el('button', { class: 'btn btn-danger btn-sm', type: 'button' }, 'Delete');
    delBtn.addEventListener('click', async () => {
      const ok = await modalConfirm({
        title: 'Delete announcement',
        message: `Delete announcement "${a.title}"? This cannot be undone.`,
        variant: 'danger', confirmText: 'Delete',
      });
      if (!ok) return;
      try {
        await eventsApi('DELETE', '/announcements/' + encodeURIComponent(a.id), { auth: true });
        await refresh(host);
      } catch (err) {
        await modalAlert({ title: 'Delete failed', message: err.message || 'Could not delete.', variant: 'danger' });
      }
    });
    actions.appendChild(delBtn);
    card.appendChild(actions);
  }
  return card;
}

function openEditModal(a, host) {
  const overlay = el('div', { class: 'modal-overlay' });
  const modal   = el('div', { class: 'modal panel', style: 'max-width:560px;width:100%;' });
  modal.innerHTML = `
    <h3>Edit announcement</h3>
    <form id="ann-edit" autocomplete="off">
      <div class="ch-form-grid">
        <div class="field span-2"><label>Title</label>
          <input name="title" required value="${escapeHtml(a.title)}" /></div>
        <div class="field span-2"><label>Body</label>
          <textarea name="body" rows="4">${escapeHtml(a.body || '')}</textarea></div>
      </div>
      <div style="display:flex;gap:.5rem;justify-content:flex-end;margin-top:1rem;">
        <button class="btn btn-ghost" type="button" id="ann-cancel">Cancel</button>
        <button class="btn"           type="submit" id="ann-save">Save</button>
      </div>
      <div id="ann-edit-msg" class="msg" style="margin-top:.5rem;"></div>
    </form>
  `;
  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  modal.querySelector('#ann-cancel').addEventListener('click', () => overlay.remove());
  overlay.addEventListener('click', (ev) => { if (ev.target === overlay) overlay.remove(); });

  modal.querySelector('#ann-edit').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const form = ev.currentTarget;
    const msg  = modal.querySelector('#ann-edit-msg');
    const btn  = modal.querySelector('#ann-save');
    msg.classList.remove('show', 'error', 'success');

    const title = form.elements.title.value.trim();
    const body  = form.elements.body.value;
    if (!title) {
      msg.classList.add('show', 'error'); msg.textContent = 'Title is required.';
      return;
    }
    btn.disabled = true; btn.textContent = 'Saving…';
    try {
      await eventsApi('PUT', '/announcements/' + encodeURIComponent(a.id), {
        auth: true, body: { title, body },
      });
      overlay.remove();
      await refresh(host);
    } catch (err) {
      msg.classList.add('show', 'error'); msg.textContent = err.message || 'Save failed.';
      btn.disabled = false; btn.textContent = 'Save';
    }
  });
}
