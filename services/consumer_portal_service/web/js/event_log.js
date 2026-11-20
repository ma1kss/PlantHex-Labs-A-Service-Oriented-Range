import { state } from './state.js';
import { data as dataApi, DATA_PREFIX } from './http.js';
import { el, escapeHtml } from './dom.js';
import { openModal, modalConfirm, modalAlert } from './modal.js';
import { subscribe } from './notifications.js';

const PAGE_SIZE = 100;

// Local view state (kept separate from the imported app `state`, which holds
// the auth token / current user).
const logState = {
  rows:   [],
  total:  0,
  picked: new Set(),
};

export async function renderEventLog(outlet) {
  outlet.appendChild(el('div', { class: 'page-head' },
    el('h1', {}, 'Event log'),
    el('p',  {}, 'Stored operational record — every message on the cyber_range.events bus, captured by Data & Logging.'),
  ));

  const toolbar = el('div', { class: 'event-toolbar' });
  toolbar.innerHTML = `
    <label class="event-checkbox"><input type="checkbox" id="ev-pickall" /> select all on this page</label>
    <span class="spacer"></span>
    <span class="hint mono" id="ev-count"></span>
    <button class="btn btn-ghost btn-sm" type="button" id="ev-refresh">Refresh</button>
    <button class="btn btn-ghost btn-sm" type="button" id="ev-export">Export</button>
    <button class="btn btn-danger btn-sm" type="button" id="ev-del-selected" disabled>Delete selected</button>
    <button class="btn btn-danger btn-sm" type="button" id="ev-del-all">Delete all</button>
  `;
  outlet.appendChild(toolbar);

  const wrap = el('div', { class: 'table-wrap', style: 'margin-top:.6rem;' });
  wrap.innerHTML = `
    <table>
      <thead><tr>
        <th style="width:34px;"></th>
        <th style="width:170px;">When</th>
        <th>Routing key</th>
        <th>Service</th>
        <th>Payload</th>
        <th style="text-align:right;width:80px;"></th>
      </tr></thead>
      <tbody id="ev-body">
        <tr><td colspan="6" class="empty"><span class="spinner"></span> loading…</td></tr>
      </tbody>
    </table>
  `;
  outlet.appendChild(wrap);

  const body = wrap.querySelector('#ev-body');
  await loadAndPaint(body);

  const off = subscribe((env) => {
    if (env.type !== 'event') return;
    // Optimistic prepend so the admin sees the bus tick immediately; a real
    // refresh re-syncs against the stored (searchable) records.
    logState.rows.unshift({
      id:             `live-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      routing_key:    env.routing_key,
      service:        env.service,
      correlation_id: env.payload?.correlation_id || '',
      payload:        env.payload || {},
      timestamp:      env.timestamp || new Date().toISOString(),
    });
    logState.total += 1;
    paintRows(body);
  });
  outlet.addEventListener('sor:teardown', off, { once: true });

  toolbar.querySelector('#ev-refresh').addEventListener('click', () => loadAndPaint(body));
  toolbar.querySelector('#ev-export').addEventListener('click', async () => {
    const fmt = await openModal({
      title: 'Export records',
      message: 'Choose a format for the stored operational records.',
      variant: 'info',
      actions: [
        { label: 'Cancel', value: null, ghost: true },
        { label: 'CSV',    value: 'csv' },
        { label: 'JSON',   value: 'json' },
        { label: 'NDJSON', value: 'ndjson', autofocus: true },
      ],
    });
    if (!['ndjson', 'json', 'csv'].includes(fmt)) return;
    exportRecords(fmt);
  });
  toolbar.querySelector('#ev-pickall').addEventListener('change', (ev) => {
    logState.picked.clear();
    if (ev.currentTarget.checked) {
      logState.rows.forEach((r) => logState.picked.add(r.id));
    }
    paintRows(body);
  });
  toolbar.querySelector('#ev-del-selected').addEventListener('click', async () => {
    const ids = [...logState.picked].filter((id) => !id.startsWith('live-'));
    if (!ids.length) return;
    const ok = await modalConfirm({
      title: 'Delete records',
      message: `Delete ${ids.length} selected record(s)? This cannot be undone.`,
      variant: 'danger', confirmText: 'Delete',
    });
    if (!ok) return;
    try {
      await dataApi('DELETE', '/logs', { auth: true, body: { ids } });
      logState.picked.clear();
      await loadAndPaint(body);
    } catch (err) {
      await modalAlert({ title: 'Delete failed', message: err.message || 'Could not delete.', variant: 'danger' });
    }
  });
  toolbar.querySelector('#ev-del-all').addEventListener('click', async () => {
    const ok = await modalConfirm({
      title: 'Delete all records',
      message: 'Delete every stored record? This cannot be undone.',
      variant: 'danger', confirmText: 'Delete all',
    });
    if (!ok) return;
    try {
      await dataApi('DELETE', '/logs', { auth: true, body: { all: true } });
      logState.picked.clear();
      await loadAndPaint(body);
    } catch (err) {
      await modalAlert({ title: 'Delete failed', message: err.message || 'Could not delete.', variant: 'danger' });
    }
  });
}

// Controlled export (F12) — token-authed download streamed by the service.
async function exportRecords(fmt) {
  try {
    const res = await fetch(`${DATA_PREFIX}/logs/export?fmt=${encodeURIComponent(fmt)}`, {
      headers: state.token ? { Authorization: 'Bearer ' + state.token } : {},
    });
    if (!res.ok) { await modalAlert({ title: 'Export failed', message: 'HTTP ' + res.status, variant: 'danger' }); return; }
    const blob = await res.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url;
    a.download = `sor-records.${fmt}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    await modalAlert({ title: 'Export failed', message: err.message || 'Could not export.', variant: 'danger' });
  }
}

async function loadAndPaint(body) {
  body.innerHTML = '<tr><td colspan="6" class="empty"><span class="spinner"></span> loading…</td></tr>';
  try {
    const page = await dataApi('GET', `/logs?limit=${PAGE_SIZE}&offset=0`, { auth: true });
    logState.rows  = page.items || [];
    logState.total = page.total || 0;
  } catch (err) {
    body.innerHTML = `<tr><td colspan="6" class="empty">Failed to load: ${escapeHtml(err.message)}</td></tr>`;
    return;
  }
  paintRows(body);
}

function paintRows(body) {
  const countEl = document.getElementById('ev-count');
  if (countEl) countEl.textContent = `showing ${logState.rows.length} / ${logState.total}`;

  const delBtn = document.getElementById('ev-del-selected');
  if (delBtn) {
    const persisted = [...logState.picked].filter((id) => !id.startsWith('live-')).length;
    delBtn.disabled = persisted === 0;
  }

  if (!logState.rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty">No records stored yet.</td></tr>';
    return;
  }

  body.innerHTML = '';
  logState.rows.forEach((r) => {
    const tr = el('tr');
    const ts = new Date(r.timestamp);
    const tsStr = isNaN(ts) ? r.timestamp : ts.toLocaleString();
    const picked = logState.picked.has(r.id);
    const live = String(r.id).startsWith('live-');
    const summary = renderPayload(r.payload);

    tr.innerHTML = `
      <td><input type="checkbox" class="ev-pick" data-id="${escapeHtml(r.id)}" ${picked ? 'checked' : ''} ${live ? 'disabled title="live row — refresh to persist"' : ''}></td>
      <td class="mono" style="font-size:.78rem;">${escapeHtml(tsStr)}</td>
      <td class="mono" style="font-size:.82rem;">${escapeHtml(r.routing_key)}</td>
      <td class="mono" style="font-size:.78rem;">${escapeHtml(r.service || '—')}</td>
      <td><div class="event-payload mono">${summary}</div></td>
      <td style="text-align:right;">
        ${live ? '<span class="badge ok" style="font-size:.62rem;">live</span>'
               : `<button class="btn btn-danger btn-sm" data-del="${escapeHtml(r.id)}">×</button>`}
      </td>
    `;
    body.appendChild(tr);
  });

  body.querySelectorAll('.ev-pick').forEach((cb) => {
    cb.addEventListener('change', () => {
      const id = cb.dataset.id;
      if (cb.checked) logState.picked.add(id); else logState.picked.delete(id);
      const delBtnInner = document.getElementById('ev-del-selected');
      if (delBtnInner) {
        const persisted = [...logState.picked].filter((x) => !x.startsWith('live-')).length;
        delBtnInner.disabled = persisted === 0;
      }
    });
  });

  body.querySelectorAll('[data-del]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.del;
      btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>';
      try {
        await dataApi('DELETE', '/logs/' + encodeURIComponent(id), { auth: true });
        logState.picked.delete(id);
        await loadAndPaint(body);
      } catch (err) {
        await modalAlert({ title: 'Delete failed', message: err.message || 'Could not delete.', variant: 'danger' });
        btn.disabled = false; btn.textContent = '×';
      }
    });
  });
}

function renderPayload(payload) {
  if (!payload || typeof payload !== 'object') return '';
  const omit = new Set(['service', 'timestamp', 'correlation_id', 'event']);
  const pairs = Object.entries(payload)
    .filter(([k]) => !omit.has(k))
    .slice(0, 6)
    .map(([k, v]) => `${escapeHtml(k)}=${escapeHtml(typeof v === 'string' ? v : JSON.stringify(v))}`);
  const summary = pairs.join(' · ');
  return `<span title="${escapeHtml(JSON.stringify(payload, null, 2))}">${summary || '<span class="hint">—</span>'}</span>`;
}
