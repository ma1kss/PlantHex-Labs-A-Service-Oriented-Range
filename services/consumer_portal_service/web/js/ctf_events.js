import { hasAnyRole } from './state.js';
import { scenario } from './http.js';
import { el, escapeHtml } from './dom.js';
import { modalConfirm, modalAlert } from './modal.js';
import { buildCard, refreshSolved, refreshMySessions } from './challenges.js';

const EDITOR_ROLES = ['admin', 'instructor'];
const isEditor = () => hasAnyRole(...EDITOR_ROLES);

// One ticking timer per rendered view; cleared on navigation (sor:teardown)
// and re-armed by each render.
let tickTimer = null;

const STATUS_BADGE = {
  active:   { cls: 'ok',          label: 'live' },
  upcoming: { cls: 'role-medium', label: 'upcoming' },
  ended:    { cls: 'muted',       label: 'ended' },
};

// Entry point

export async function renderCtfEvents(outlet) {
  outlet.appendChild(el('div', { class: 'page-head' },
    el('h1', {}, 'Events'),
    el('p', {}, isEditor()
      ? 'Time-boxed competitions built from the challenge pool. Players can play their challenges only while the clock runs.'
      : 'Competitions with a start and an end. Challenges open when the event goes live and close when it ends.'),
  ));

  const content = el('div');
  outlet.appendChild(content);
  outlet.addEventListener('sor:teardown', stopTicking, { once: true });

  await renderList(content);
}

function stopTicking() {
  if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
}

// Time helpers

const fmt = (iso) => new Date(iso).toLocaleString([], {
  year: 'numeric', month: 'short', day: 'numeric',
  hour: '2-digit', minute: '2-digit',
});

function remainingText(ev) {
  const now = Date.now();
  const start = new Date(ev.starts_at).getTime();
  const end   = new Date(ev.ends_at).getTime();
  const span = (ms) => {
    const s = Math.max(0, Math.floor(ms / 1000));
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600),
          m = Math.floor((s % 3600) / 60), sec = s % 60;
    if (d > 0) return `${d}d ${h}h ${m}m`;
    if (h > 0) return `${h}h ${m}m ${sec}s`;
    return `${m}m ${sec}s`;
  };
  if (now < start) return `starts in ${span(start - now)}`;
  if (now <= end)  return `ends in ${span(end - now)}`;
  return `ended ${fmt(ev.ends_at)}`;
}

// Live-update every countdown on the page; when any event crosses a window
// boundary, reload the whole view so status badges and access flip over.
function armTicker(events, reload) {
  stopTicking();
  const statusNow = (ev) => {
    const now = Date.now();
    if (now < new Date(ev.starts_at).getTime()) return 'upcoming';
    if (now > new Date(ev.ends_at).getTime())   return 'ended';
    return 'active';
  };
  const initial = new Map(events.map((ev) => [ev.id, statusNow(ev)]));
  tickTimer = setInterval(() => {
    for (const ev of events) {
      document.querySelectorAll(`[data-evt-countdown="${ev.id}"]`)
        .forEach((n) => { n.textContent = remainingText(ev); });
      if (statusNow(ev) !== initial.get(ev.id)) {
        stopTicking();
        reload();
        return;
      }
    }
  }, 1000);
}

// Event list

async function renderList(container) {
  container.innerHTML = '<div class="empty"><span class="spinner"></span> loading…</div>';

  let events;
  try {
    events = await scenario('GET', '/events', { auth: true });
  } catch (err) {
    container.innerHTML =
      `<div class="empty">Failed to load events: ${escapeHtml(err.message)}</div>`;
    return;
  }

  container.innerHTML = '';

  const head = el('div', { class: 'section-head' },
    el('h2', {}, 'All events'),
    el('span', { class: 'hint' }, `${events.length} event${events.length === 1 ? '' : 's'}`),
  );
  if (isEditor()) {
    const newBtn = el('button', { class: 'btn', type: 'button', style: 'margin-left:auto;' }, '+ New event');
    newBtn.addEventListener('click', () => openEventModal(null, () => renderList(container)));
    head.appendChild(newBtn);
  }
  container.appendChild(head);

  if (!events.length) {
    container.appendChild(el('div', { class: 'empty' }, isEditor()
      ? 'No events yet — create the first one.'
      : 'No events scheduled.'));
    return;
  }

  const grid = el('div', { class: 'services-grid' });
  events.forEach((ev) => grid.appendChild(buildEventCard(ev, container)));
  container.appendChild(grid);

  armTicker(events, () => renderList(container));
}

function buildEventCard(ev, listContainer) {
  const badge = STATUS_BADGE[ev.status] || STATUS_BADGE.ended;
  const card  = el('div', { class: 'svc-tile', style: 'display:flex;flex-direction:column;gap:.5rem;cursor:pointer;' });

  card.innerHTML = `
    <div class="row">
      <span class="svc-name">${escapeHtml(ev.name)}</span>
      <span class="badge ${badge.cls}">${badge.label}</span>
    </div>
    <div class="svc-code">
      ${fmt(ev.starts_at)} → ${fmt(ev.ends_at)}
    </div>
    <div class="svc-code" data-evt-countdown="${escapeHtml(ev.id)}">${remainingText(ev)}</div>
    <div class="svc-desc">${escapeHtml(ev.description || '—')}</div>
    <div style="display:flex;gap:.3rem;flex-wrap:wrap;">
      <span class="badge muted">${ev.challenge_count} challenge${ev.challenge_count === 1 ? '' : 's'}</span>
      ${ev.author ? `<span class="badge muted">by ${escapeHtml(ev.author)}</span>` : ''}
    </div>
  `;

  const actions = el('div', { class: 'card-actions', style: 'display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.5rem;' });
  const openBtn = el('button', { class: 'btn', type: 'button' }, 'Open');
  openBtn.addEventListener('click', (e) => { e.stopPropagation(); renderDetail(listContainer, ev.id); });
  actions.appendChild(openBtn);

  if (isEditor()) {
    const editBtn = el('button', { class: 'btn btn-ghost', type: 'button' }, 'Edit');
    editBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      openEventModal(ev.id, () => renderList(listContainer));
    });
    actions.appendChild(editBtn);

    const delBtn = el('button', { class: 'btn btn-danger', type: 'button' }, 'Delete');
    delBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const ok = await modalConfirm({
        title: 'Delete event',
        message: `Delete event "${ev.name}"? Its challenges are not deleted — they return to the playground.`,
        variant: 'danger', confirmText: 'Delete',
      });
      if (!ok) return;
      try {
        await scenario('DELETE', `/events/${encodeURIComponent(ev.id)}`, { auth: true });
        await renderList(listContainer);
      } catch (err) {
        await modalAlert({ title: 'Delete failed', message: err.message || 'Could not delete.', variant: 'danger' });
      }
    });
    actions.appendChild(delBtn);
  }

  card.appendChild(actions);
  card.addEventListener('click', () => renderDetail(listContainer, ev.id));
  return card;
}

// Event detail (the playing surface while live)

async function renderDetail(container, eventId) {
  container.innerHTML = '<div class="empty"><span class="spinner"></span> loading…</div>';

  let ev;
  try {
    ev = await scenario('GET', `/events/${encodeURIComponent(eventId)}`, { auth: true });
  } catch (err) {
    container.innerHTML =
      `<div class="empty">Failed to load event: ${escapeHtml(err.message)}</div>`;
    return;
  }

  container.innerHTML = '';

  const back = el('button', { class: 'btn btn-ghost btn-sm', type: 'button', style: 'margin-bottom:.75rem;' }, '← All events');
  back.addEventListener('click', () => renderList(container));
  container.appendChild(back);

  const badge = STATUS_BADGE[ev.status] || STATUS_BADGE.ended;
  const head = el('div', { class: 'section-head' });
  head.innerHTML = `
    <h2>${escapeHtml(ev.name)} <span class="badge ${badge.cls}">${badge.label}</span></h2>
    <span class="hint" data-evt-countdown="${escapeHtml(ev.id)}">${remainingText(ev)}</span>
  `;
  container.appendChild(head);

  container.appendChild(el('div', { class: 'svc-code', style: 'margin-bottom:.35rem;' },
    `${fmt(ev.starts_at)} → ${fmt(ev.ends_at)}`));
  if (ev.description) {
    container.appendChild(el('p', { class: 'svc-desc', style: 'margin:0 0 1rem;' }, ev.description));
  }

  const reload = () => renderDetail(container, eventId);

  if (!ev.accessible) {
    container.appendChild(el('div', { class: 'empty' },
      ev.status === 'upcoming'
        ? `This event has not started yet — its ${ev.challenge_count} challenge${ev.challenge_count === 1 ? ' opens' : 's open'} when the clock hits zero.`
        : 'This event has ended — its challenges are no longer accessible.'));
    armTicker([ev], reload);
    return;
  }

  if (!ev.challenges.length) {
    container.appendChild(el('div', { class: 'empty' }, 'No challenges in this event yet.'));
    armTicker([ev], reload);
    return;
  }

  // Same interactive cards as the playground — sessions, download, flag form.
  await Promise.all([refreshSolved(), refreshMySessions()]);
  const grid = el('div', { class: 'services-grid' });
  ev.challenges.forEach((c) => grid.appendChild(buildCard(c, container, reload)));
  container.appendChild(grid);

  armTicker([ev], reload);
}

// Create / edit modal (admin / instructor)

const toLocalInput = (iso) => {
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};

async function openEventModal(eventId, onSaved) {
  // Editing needs the current membership; creating starts blank.
  let current = null;
  if (eventId) {
    try {
      current = await scenario('GET', `/events/${encodeURIComponent(eventId)}`, { auth: true });
    } catch (err) {
      await modalAlert({ title: 'Load failed', message: err.message || 'Could not load the event.', variant: 'danger' });
      return;
    }
  }

  let challenges = [];
  try {
    challenges = await scenario('GET', '/challenges', { auth: true });
  } catch { /* selection list degrades to empty */ }

  const selected = new Set(current?.challenge_ids || []);

  const overlay = el('div', { class: 'modal-overlay' });
  const modal   = el('div', { class: 'modal panel', style: 'max-width:640px;width:100%;' });

  // Default window for new events: starts in 1h, runs 24h.
  const defStart = new Date(Date.now() + 3600_000);
  const defEnd   = new Date(Date.now() + 25 * 3600_000);

  modal.innerHTML = `
    <h3>${eventId ? 'Edit event' : 'Create event'}</h3>
    <form id="evt-form">
      <div class="ch-form-grid">
        <div class="field span-2"><label>Name</label>
          <input name="name" required maxlength="255"
                 value="${escapeHtml(current?.name || '')}" /></div>
        <div class="field span-2"><label>Description</label>
          <textarea name="description" rows="2">${escapeHtml(current?.description || '')}</textarea></div>
        <div class="field"><label>Starts</label>
          <input name="starts_at" type="datetime-local" required
                 value="${toLocalInput(current?.starts_at || defStart)}" /></div>
        <div class="field"><label>Ends</label>
          <input name="ends_at" type="datetime-local" required
                 value="${toLocalInput(current?.ends_at || defEnd)}" /></div>
      </div>

      <div class="field" style="margin-top:.75rem;">
        <label>Included challenges
          <span class="hint" id="evt-sel-count" style="margin-left:.4rem;"></span></label>
        <div id="evt-chal-list"
             style="max-height:260px;overflow:auto;border:1px solid var(--border);border-radius:8px;padding:.5rem .75rem;display:flex;flex-direction:column;gap:.35rem;">
        </div>
        <small class="hint">Included challenges leave the playground and are playable only while this event is live.</small>
      </div>

      <div style="display:flex;gap:.5rem;justify-content:flex-end;margin-top:1rem;">
        <button class="btn btn-ghost" type="button" id="evt-cancel">Cancel</button>
        <button class="btn"           type="submit" id="evt-save">${eventId ? 'Save' : 'Create'}</button>
      </div>
      <div id="evt-msg" class="msg" style="margin-top:.5rem;"></div>
    </form>
  `;
  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  const listBox  = modal.querySelector('#evt-chal-list');
  const selCount = modal.querySelector('#evt-sel-count');
  const paintCount = () => { selCount.textContent = `(${selected.size} selected)`; };
  paintCount();

  if (!challenges.length) {
    listBox.innerHTML = '<span class="hint">No challenges available — create some first.</span>';
  } else {
    challenges.forEach((c) => {
      const row = el('label', { style: 'display:flex;align-items:center;gap:.55rem;font-size:.85rem;cursor:pointer;' });
      const cb  = el('input', { type: 'checkbox' });
      cb.checked = selected.has(c.id);
      cb.addEventListener('change', () => {
        if (cb.checked) selected.add(c.id); else selected.delete(c.id);
        paintCount();
      });
      row.appendChild(cb);
      row.appendChild(el('span', {}, c.title));
      row.appendChild(el('span', { class: `badge role-${c.difficulty}` }, c.difficulty));
      row.appendChild(el('span', { class: 'badge muted' }, `${c.points} pts`));
      if (c.visible === false) row.appendChild(el('span', { class: 'badge muted' }, 'hidden'));
      listBox.appendChild(row);
    });
  }

  modal.querySelector('#evt-cancel').addEventListener('click', () => overlay.remove());
  overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) overlay.remove(); });

  modal.querySelector('#evt-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const msg  = modal.querySelector('#evt-msg');
    const btn  = modal.querySelector('#evt-save');
    msg.classList.remove('show', 'error');

    const starts = new Date(form.elements.starts_at.value);
    const ends   = new Date(form.elements.ends_at.value);
    if (!(starts < ends)) {
      msg.classList.add('show', 'error');
      msg.textContent = 'The event must end after it starts.';
      return;
    }

    const body = {
      name:          form.elements.name.value.trim(),
      description:   form.elements.description.value,
      starts_at:     starts.toISOString(),
      ends_at:       ends.toISOString(),
      challenge_ids: [...selected],
    };

    btn.disabled = true;
    btn.textContent = 'Saving…';
    try {
      if (eventId) {
        await scenario('PUT', `/events/${encodeURIComponent(eventId)}`, { auth: true, body });
      } else {
        await scenario('POST', '/events', { auth: true, body });
      }
      overlay.remove();
      await onSaved();
    } catch (err) {
      msg.classList.add('show', 'error');
      msg.textContent = err.message || 'Save failed.';
      btn.disabled = false;
      btn.textContent = eventId ? 'Save' : 'Create';
    }
  });
}
