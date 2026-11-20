import { state, hasAnyRole, isAdmin } from './state.js';
import { scenario, orchestration, uploadForm } from './http.js';
import { el, escapeHtml } from './dom.js';
import { modalConfirm, modalAlert } from './modal.js';

const EDITOR_ROLES = ['admin', 'instructor'];
const DIFFICULTIES = ['easy', 'medium', 'hard'];
const TYPES        = ['static', 'docker', 'lab'];

const isEditor = () => hasAnyRole(...EDITOR_ROLES);
const isDocker = (c) =>
  !!(c && (c.is_docker
        || (c.challenge_type || '').toLowerCase() === 'docker'
        || (c.category       || '').toLowerCase() === 'docker'));
const isLab = (c) =>
  !!(c && (c.is_lab || (c.challenge_type || '').toLowerCase() === 'lab'));
// Anything that needs a per-player instance (single container or a lab).
const needsSession = (c) => isDocker(c) || isLab(c);

// Module-level cache so cross-tab state survives re-renders
const cache = {
  solved:        new Set(),    // challenge ids the current user has solved
  mySessions:    new Map(),    // challenge_id → DockerSessionOut (running)
};

// Entry point

export async function renderChallenges(outlet) {
  outlet.appendChild(el('div', { class: 'page-head' },
    el('h1', {}, 'Challenges'),
    el('p',  {}, isEditor()
      ? 'Review every challenge, build new ones, and watch player sessions live.'
      : 'Pick a challenge — start a session if it needs one, find the flag, submit it.'),
  ));

  // Sub-tab bar.
  const TABS = isEditor()
    ? [
        { id: 'list',     label: 'Challenges' },
        { id: 'create',   label: 'Create' },
        { id: 'sessions', label: 'Live sessions' },
      ]
    : [
        { id: 'list',     label: 'Challenges' },
      ];

  const tabBar  = el('div', { class: 'subtabs' });
  const content = el('div', { id: 'tab-content' });
  TABS.forEach((t) => {
    const btn = el('button',
      { type: 'button', class: 'subtab', 'data-tab': t.id }, t.label);
    btn.addEventListener('click', () => showTab(t.id, tabBar, content));
    tabBar.appendChild(btn);
  });
  outlet.appendChild(tabBar);
  outlet.appendChild(content);

  // Prime caches before painting the default tab.
  await Promise.all([
    refreshSolved(),
    refreshMySessions(),
  ]);
  await showTab('list', tabBar, content);
}

async function showTab(id, tabBar, content) {
  tabBar.querySelectorAll('.subtab').forEach((b) =>
    b.classList.toggle('active', b.dataset.tab === id));
  content.innerHTML = '';

  if (id === 'list') {
    await renderListTab(content);
  } else if (id === 'create' && isEditor()) {
    renderCreateTab(content);
  } else if (id === 'sessions' && isEditor()) {
    await renderSessionsTab(content);
  }
}

// Caches

export async function refreshSolved() {
  try {
    const data = await scenario('GET', '/my-solves', { auth: true });
    cache.solved = new Set(data.solved || []);
  } catch {
    cache.solved = new Set();
  }
}

export async function refreshMySessions() {
  try {
    const list = await scenario('GET', '/sessions', { auth: true });
    cache.mySessions = new Map();
    (list || [])
      .filter((s) => s.status === 'running' && s.user_id === state.me.id)
      .forEach((s) => cache.mySessions.set(s.challenge_id, s));
  } catch {
    cache.mySessions = new Map();
  }
}

// List tab

async function renderListTab(container) {
  container.innerHTML = '<div class="empty"><span class="spinner"></span> loading…</div>';

  let challenges;
  try {
    challenges = await scenario('GET', '/challenges', { auth: true });
  } catch (err) {
    container.innerHTML =
      `<div class="empty">Failed to load challenges: ${escapeHtml(err.message)}</div>`;
    return;
  }

  if (!challenges.length) {
    container.innerHTML = '<div class="empty">No challenges yet.</div>';
    return;
  }

  container.innerHTML = '';
  const grid = el('div', { class: 'services-grid' });
  challenges.forEach((c) => grid.appendChild(buildCard(c, container)));
  container.appendChild(grid);
}

// `onChanged` lets other views (the Events tab) reuse the card: it is called
// after an editor action mutates the challenge, instead of re-rendering the
// playground list.
export function buildCard(c, listContainer, onChanged = null) {
  const rerender = onChanged || (() => renderListTab(listContainer));
  const card = el('div', { class: 'svc-tile challenge-tile', style: 'display:flex;flex-direction:column;gap:.5rem;' });
  card.dataset.challengeId = c.id;

  const docker     = isDocker(c);
  const lab        = isLab(c);
  const solved     = cache.solved.has(c.id);
  if (solved) card.classList.add('solved');

  const tags = (c.tags || []).map(t =>
    `<span class="badge muted">${escapeHtml(t)}</span>`).join(' ');

  // Type badge is admin-only — players don't need it.
  const typeName  = lab ? 'lab' : docker ? 'docker' : 'static';
  const typeBadge = isEditor()
    ? `<span class="badge type-${typeName}">${typeName}</span>`
    : '';

  let fileBadge = '';
  if (lab) {
    fileBadge = `<span class="badge type-lab">${c.lab_services || '?'} service${(c.lab_services === 1) ? '' : 's'} · VPN</span>`;
  } else if (c.file_name) {
    fileBadge = `<span class="badge ok">file: ${escapeHtml(c.file_name)}</span>`;
  } else if (docker && isEditor()) {
    const ports = (c.docker?.exposed_ports || c.docker_ports || []).join(', ') || '—';
    fileBadge = `<span class="badge muted">image · ports: ${escapeHtml(ports)}</span>`;
  }

  const dockerEditorMeta = (docker && isEditor() && c.docker)
    ? `<div class="svc-code" style="word-break:break-all;">
         image: ${escapeHtml(c.docker.image_tag)}<br/>
         base: ${escapeHtml(c.docker.base_image)} · cpu ${c.docker.cpu_limit} · mem ${escapeHtml(c.docker.memory_limit)} · max/p ${c.docker.max_instances_per_player}
       </div>`
    : '';

  const solvedRibbon = solved
    ? '<span class="solved-ribbon" title="You solved this">✓ solved</span>'
    : '';
  const hiddenBadge = (isEditor() && c.visible === false)
    ? '<span class="badge muted" title="Hidden from players">hidden</span>'
    : '';

  card.innerHTML = `
    <div class="row">
      <span class="svc-name">${escapeHtml(c.title)} ${solvedRibbon} ${hiddenBadge}</span>
      <span class="badge role-${escapeHtml(c.difficulty)}">${escapeHtml(c.difficulty)}</span>
    </div>
    <div class="svc-code" style="display:flex;align-items:center;gap:.4rem;flex-wrap:wrap;">
      ${typeBadge}
      <span>${escapeHtml(c.category)} · ${c.points} pts${
        c.max_attempts ? ` · max ${c.max_attempts} attempts` : ''
      }</span>
    </div>
    <div class="svc-desc">${escapeHtml(c.description || '—')}</div>
    <div style="display:flex;flex-wrap:wrap;gap:.3rem;">${fileBadge} ${tags}</div>
    ${dockerEditorMeta}
  `;

  // Top-level action buttons: start session / download / edit / delete.
  const actions = el('div', { class: 'card-actions', style: 'display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.5rem;' });

  if (needsSession(c)) {
    actions.appendChild(buildSessionControl(c));
  }

  if (c.file_name) {
    const dl = el('button', { class: 'btn btn-ghost', type: 'button' }, 'Download');
    dl.addEventListener('click', () => downloadChallenge(c));
    actions.appendChild(dl);
  }

  if (isEditor()) {
    // Visibility toggle — show/hide this challenge from players.
    const visible = c.visible !== false;
    const visBtn = el('button', { class: 'btn btn-ghost btn-sm', type: 'button' },
      visible ? '🙈 Hide' : '👁 Show');
    visBtn.title = visible ? 'Visible to players — click to hide' : 'Hidden from players — click to show';
    visBtn.addEventListener('click', async () => {
      visBtn.disabled = true;
      try {
        await scenario('POST', `/challenges/${encodeURIComponent(c.id)}/visibility`,
          { auth: true, body: { visible: !visible } });
        await rerender();
      } catch (err) {
        visBtn.disabled = false;
        await modalAlert({ title: 'Update failed', message: err.message || 'Could not change visibility.', variant: 'danger' });
      }
    });
    actions.appendChild(visBtn);

    const editBtn = el('button', { class: 'btn btn-ghost', type: 'button' }, 'Edit');
    editBtn.addEventListener('click', () => openEditModal(c, listContainer, onChanged));
    actions.appendChild(editBtn);

    const delBtn = el('button', { class: 'btn btn-danger', type: 'button' }, 'Delete');
    delBtn.addEventListener('click', async () => {
      const extra = lab ? ' This also stops any running instances and removes their networks.'
                  : docker ? ' This also removes the image and stops any running sessions.'
                  : ' This also removes its file.';
      const ok = await modalConfirm({
        title: 'Delete challenge',
        message: `Delete challenge "${c.title}"?${extra}`,
        variant: 'danger', confirmText: 'Delete',
      });
      if (!ok) return;
      try {
        await scenario('DELETE', `/challenges/${encodeURIComponent(c.id)}`, { auth: true });
        await refreshMySessions();
        await rerender();
      } catch (err) {
        await modalAlert({ title: 'Delete failed', message: err.message || 'Could not delete.', variant: 'danger' });
      }
    });
    actions.appendChild(delBtn);
  }

  card.appendChild(actions);

  // Inline endpoint line — only when this card has a live session for the user.
  const endpointSlot = el('div', { class: 'endpoint-slot' });
  card.appendChild(endpointSlot);
  paintEndpoint(endpointSlot, c, card);

  // Inline flag-submission form.
  const flagForm = el('form', { class: 'flag-form', autocomplete: 'off' });
  flagForm.innerHTML = `
    <input name="flag" type="text" placeholder="flag{...}" autocomplete="off" required />
    <button class="btn" type="submit">Submit</button>
  `;
  flagForm.addEventListener('submit', (ev) => {
    ev.preventDefault();
    submitFlagInline(c, card, flagForm.elements.flag, flagForm.querySelector('button'));
  });
  card.appendChild(flagForm);

  // Toast slot — used for success / error feedback.
  card.appendChild(el('div', { class: 'msg toast-slot', style: 'margin-top:.4rem;' }));

  return card;
}

// Session controls on each card

function buildSessionControl(c) {
  const lab = isLab(c);
  const session = cache.mySessions.get(c.id);
  if (session) {
    const btn = el('button', { class: 'btn btn-danger', type: 'button' }, lab ? 'Stop lab' : 'Stop session');
    btn.addEventListener('click', () => stopSession(session.id, c.id, btn));
    return btn;
  }
  const btn = el('button', { class: 'btn', type: 'button' }, lab ? 'Start lab' : 'Start session');
  btn.addEventListener('click', () => startSession(c, btn));
  return btn;
}

function endpointFor(session) {
  // First mapped host port; players don't need to see the container-side port.
  const hostPort = Object.values(session.ports || {}).find(Boolean);
  if (!hostPort) return '';
  return `${session.hostname || 'localhost'}:${hostPort}`;
}

function paintEndpoint(slot, c, cardNode) {
  slot.innerHTML = '';
  const session = cache.mySessions.get(c.id);
  if (!session) return;

  // Labs are reached over WireGuard — show the VPN config download plus one
  // endpoint per service (each container has its own IP on the lab subnet).
  if ((session.kind || 'single') === 'lab') {
    const wrap = el('div', { class: 'endpoint-line', style: 'flex-wrap:wrap;gap:.5rem;' });
    wrap.innerHTML = `
      <span class="endpoint-label">VPN</span>
      <code class="endpoint-host">${escapeHtml(session.subnet || 'lab network')}</code>
      <span class="badge ${session.status === 'running' ? 'ok' : 'muted'}">${escapeHtml(session.status)}</span>
    `;
    const dl = el('button', { class: 'btn btn-sm', type: 'button' }, '⤓ Download VPN config');
    dl.addEventListener('click', () => downloadWireguardConf(session));
    wrap.appendChild(dl);
    slot.appendChild(wrap);

    const eps = session.lab_endpoints || [];
    if (eps.length) {
      const box = el('div', { class: 'lab-endpoints' });
      box.innerHTML = `
        <div class="lab-endpoints-head">Lab services — reach these over the VPN</div>
        <table class="lab-endpoints-table">
          <thead><tr><th>Service</th><th>Address</th><th>Ports</th></tr></thead>
          <tbody>${eps.map((e) => {
            const ports = (e.ports && e.ports.length)
              ? e.ports.map((p) => {
                  const num = String(p).split('/')[0];
                  return `<code class="ep-port" data-copy="${escapeHtml(e.ip)}:${escapeHtml(num)}">${escapeHtml(p)}</code>`;
                }).join(' ')
              : '<span class="hint">—</span>';
            return `<tr>
              <td><strong>${escapeHtml(e.name)}</strong></td>
              <td><code class="ep-ip" data-copy="${escapeHtml(e.ip)}">${escapeHtml(e.ip)}</code></td>
              <td>${ports}</td>
            </tr>`;
          }).join('')}</tbody>
        </table>`;
      // Click any address/port to copy the connect target to the clipboard.
      box.querySelectorAll('[data-copy]').forEach((n) => {
        n.style.cursor = 'pointer';
        n.title = 'Click to copy';
        n.addEventListener('click', () => {
          navigator.clipboard?.writeText(n.dataset.copy).catch(() => {});
          n.classList.add('copied');
          setTimeout(() => n.classList.remove('copied'), 800);
        });
      });
      slot.appendChild(box);
    }
    return;
  }

  const ep = endpointFor(session);
  if (!ep) return;

  const wrap = el('div', { class: 'endpoint-line' });
  wrap.innerHTML = `
    <span class="endpoint-label">endpoint</span>
    <code class="endpoint-host">${escapeHtml(ep)}</code>
    <span class="badge ${session.status === 'running' ? 'ok' : 'muted'}">${escapeHtml(session.status)}</span>
  `;
  slot.appendChild(wrap);
}

async function downloadWireguardConf(session) {
  try {
    const res = await fetch(`/scenario/sessions/${encodeURIComponent(session.id)}/wireguard.conf`, {
      headers: state.token ? { Authorization: 'Bearer ' + state.token } : {},
    });
    if (!res.ok) {
      await modalAlert({ title: 'Download failed', message: 'HTTP ' + res.status, variant: 'danger' });
      return;
    }
    const blob = await res.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href = url;
    a.download = `sor-lab-${(session.id || '').slice(0, 8)}.conf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    await modalAlert({ title: 'Download failed', message: err.message || 'Could not download config.', variant: 'danger' });
  }
}

async function startSession(c, btn) {
  const card = document.querySelector(`.challenge-tile[data-challenge-id="${c.id}"]`);
  const result = card?.querySelector('.toast-slot');
  if (result) {
    result.classList.remove('show', 'error', 'success');
    result.classList.add('show');
    result.textContent = 'Starting your container…';
  }
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';

  try {
    // Launch is coordinated by Orchestration: it asks Scenario to deploy the
    // instance, then grants access (IAM), registers the attempt (Learning) and
    // begins observation (Monitoring). We get the deployed session back.
    const res  = await orchestration('POST', '/launches', { auth: true, body: { challenge_id: c.id } });
    const sess = res.session;
    if (sess) cache.mySessions.set(c.id, sess);
    if (result) {
      const warn = (res.warnings || []).length;
      if (warn) {
        result.classList.remove('error', 'success');
        result.classList.add('show');
        result.textContent = `Launched — ${warn} step${warn > 1 ? 's' : ''} warned.`;
        setTimeout(() => { result.classList.remove('show'); result.textContent = ''; }, 4500);
      } else {
        result.classList.remove('show', 'error', 'success');
      }
    }
    // Repaint just this card's session controls + endpoint.
    repaintCard(c);
  } catch (err) {
    if (result) {
      result.classList.remove('show', 'success');
      result.classList.add('show', 'error');
      result.textContent = err.message || 'Start failed.';
    }
    btn.disabled = false;
    btn.textContent = 'Start session';
  }
}

async function stopSession(sessionId, challengeId, btn) {
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>';
  try {
    // Teardown is coordinated by Orchestration (reverses the launch sequence,
    // ending with Scenario stopping the container).
    await orchestration('DELETE', `/sessions/${encodeURIComponent(sessionId)}`, { auth: true });
    cache.mySessions.delete(challengeId);
    repaintCard({ id: challengeId });
  } catch (err) {
    await modalAlert({ title: 'Stop failed', message: err.message || 'Could not stop the session.', variant: 'danger' });
    btn.disabled = false;
    btn.textContent = 'Stop session';
  }
}

function repaintCard(c) {
  // Re-render just the action button + endpoint slot of the named card.
  const card = document.querySelector(`.challenge-tile[data-challenge-id="${c.id}"]`);
  if (!card) return;
  const actions = card.querySelector('.card-actions');
  const slot    = card.querySelector('.endpoint-slot');
  if (!actions || !slot) return;

  // Replace the first button (session control) with a fresh one.
  const oldBtn = actions.querySelector('.btn');
  const newBtn = buildSessionControl(c);
  if (oldBtn) actions.replaceChild(newBtn, oldBtn);
  else actions.prepend(newBtn);

  paintEndpoint(slot, c, card);
}

// Inline flag submission

function submitFlagInline(c, cardNode, inputEl, buttonEl) {
  const flag   = (inputEl.value || '').trim();
  const result = cardNode.querySelector('.toast-slot');

  result.classList.remove('show', 'error', 'success');

  if (!flag) {
    result.classList.add('show', 'error');
    result.textContent = 'Enter a flag first.';
    return;
  }

  buttonEl.disabled = true;
  const original = buttonEl.textContent;
  buttonEl.innerHTML = '<span class="spinner"></span>';
  result.classList.add('show');
  result.textContent = 'Submitting…';

  scenario('POST', `/challenges/${encodeURIComponent(c.id)}/submit`, {
    body: { flag },
    auth: true,
  }).then((res) => {
    result.classList.remove('show', 'error', 'success');
    result.classList.add('show', res.correct ? 'success' : 'error');
    // Scoring is handled by Learning & Progress (the Scoreboard tab shows the
    // points). Here we just confirm the solve.
    result.textContent = res.correct ? `${res.message} ✓` : res.message;

    if (res.correct) {
      cache.solved.add(c.id);
      cardNode.classList.add('solved');
      // Add a solved ribbon if not already present.
      if (!cardNode.querySelector('.solved-ribbon')) {
        const nameEl = cardNode.querySelector('.svc-name');
        if (nameEl) {
          const r = document.createElement('span');
          r.className = 'solved-ribbon';
          r.textContent = '✓ solved';
          nameEl.appendChild(document.createTextNode(' '));
          nameEl.appendChild(r);
        }
      }
      inputEl.value = '';
      // Fade the success toast after 5 s.
      setTimeout(() => {
        result.classList.remove('show', 'success');
        result.textContent = '';
      }, 5000);
    }
  }).catch((err) => {
    result.classList.remove('show', 'error', 'success');
    result.classList.add('show', 'error');
    result.textContent = err.message || 'Submission failed.';
  }).finally(() => {
    buttonEl.disabled = false;
    buttonEl.textContent = original;
  });
}

// Trainee: download artefact

async function downloadChallenge(c) {
  const res = await fetch(`/scenario/challenges/${encodeURIComponent(c.id)}/download`, {
    headers: state.token ? { Authorization: 'Bearer ' + state.token } : {},
  });
  if (!res.ok) {
    await modalAlert({ title: 'Download failed', message: 'HTTP ' + res.status, variant: 'danger' });
    return;
  }
  const blob = await res.blob();
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = c.file_name || 'challenge.bin';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// Create tab (admin / instructor)

function renderCreateTab(container) {
  const panel = el('div', { class: 'panel' });
  panel.innerHTML = `
    <h3>Create challenge</h3>
    <form id="ch-upload" enctype="multipart/form-data">
      <div class="ch-form-grid">
        <div class="field"><label>Title</label>
          <input name="title" required maxlength="255" /></div>
        <div class="field"><label>Type</label>
          <select name="challenge_type">
            ${TYPES.map(t => `<option value="${t}">${t}</option>`).join('')}
          </select></div>
        <div class="field"><label>Category</label>
          <input name="category" placeholder="pwn, web, crypto, custom…" value="misc" required /></div>
        <div class="field"><label>Difficulty</label>
          <select name="difficulty">
            ${DIFFICULTIES.map(d => `<option value="${d}">${d}</option>`).join('')}
          </select></div>
        <div class="field"><label>Points</label>
          <input name="points" type="number" min="0" value="100" /></div>
        <div class="field"><label>Max attempts (0 = unlimited)</label>
          <input name="max_attempts" type="number" min="0" value="0" /></div>
        <div class="field"><label>Visibility</label>
          <label style="display:flex;align-items:center;gap:.5rem;margin-top:.45rem;font-size:.85rem;cursor:pointer;"><input type="checkbox" name="visible" checked /> Visible to players</label></div>
        <div class="field span-2"><label>Tags (comma-separated)</label>
          <input name="tags" placeholder="xss, beginner" /></div>
        <div class="field span-2"><label>Description</label>
          <textarea name="description" rows="3"></textarea></div>
        <div class="field"><label>Flag</label>
          <input name="flag" required placeholder="flag{...}" /></div>
        <div class="field"><label class="ch-file-label">Challenge file</label>
          <input name="file" type="file" />
          <small class="hint ch-file-hint" style="display:none;">tar / tar.gz with Dockerfile + project files</small></div>
        <div class="field ch-extra-row" style="display:none;"><label>Optional downloadable file</label>
          <input name="extra_file" type="file" />
          <small class="hint">handed to players via Download; e.g. sources, hints, sample input</small></div>
      </div>

      <fieldset class="ch-docker-fields" style="display:none;border:1px dashed var(--border);border-radius:8px;padding:.75rem 1rem;margin-top:.5rem;">
        <legend class="mono" style="padding:0 .4rem;color:var(--fg-dim);font-size:.7rem;letter-spacing:.18em;">DOCKER RUNTIME</legend>
        <div class="ch-form-grid">
          <div class="field"><label>CPU limit (cores)</label>
            <input name="docker_cpu_limit" type="number" min="0.1" step="0.1" value="1.0" /></div>
          <div class="field"><label>Memory limit</label>
            <input name="docker_memory_limit" placeholder="512m" value="512m" /></div>
          <div class="field"><label>PIDs limit</label>
            <input name="docker_pids_limit" type="number" min="50" value="200" /></div>
          <div class="field"><label>Max instances / player</label>
            <input name="docker_max_instances_per_player" type="number" min="1" value="1" /></div>
        </div>
      </fieldset>

      <div style="display:flex;gap:.6rem;align-items:center;margin-top:.75rem;">
        <button class="btn" type="submit" id="ch-upload-btn">Create</button>
        <span id="ch-upload-msg" class="msg" style="margin:0;"></span>
      </div>
    </form>
  `;
  container.appendChild(panel);

  const form         = panel.querySelector('#ch-upload');
  const typeSel      = form.elements.challenge_type;
  const dockerFields = panel.querySelector('.ch-docker-fields');
  const fileLabel    = panel.querySelector('.ch-file-label');
  const fileHint     = panel.querySelector('.ch-file-hint');
  const extraRow     = panel.querySelector('.ch-extra-row');

  const reflectType = () => {
    const t      = typeSel.value;
    const isDoc  = t === 'docker';
    const isLabT = t === 'lab';
    dockerFields.style.display = isDoc ? '' : 'none';
    // Both docker and lab challenges can ship an optional downloadable file.
    extraRow.style.display     = (isDoc || isLabT) ? '' : 'none';
    if (isLabT) {
      fileLabel.textContent  = 'Docker Compose file (.yml / .yaml)';
      fileHint.textContent   = 'Up to 5 services, each needs an image: (build: not supported). Declare ports via expose:/ports: so players see each container’s endpoint. Reached over WireGuard VPN.';
      fileHint.style.display = '';
    } else if (isDoc) {
      fileLabel.textContent  = 'Build context (.tar / .tar.gz)';
      fileHint.textContent   = 'tar / tar.gz with Dockerfile + project files';
      fileHint.style.display = '';
    } else {
      fileLabel.textContent  = 'Challenge file';
      fileHint.style.display = 'none';
    }
    form.elements.file.required = isDoc || isLabT;
  };
  typeSel.addEventListener('change', reflectType);
  reflectType();

  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const msg = panel.querySelector('#ch-upload-msg');
    const btn = panel.querySelector('#ch-upload-btn');
    msg.textContent = '';
    msg.classList.remove('show', 'error', 'success');

    const data = new FormData(form);
    if (!form.elements.file.files.length) data.delete('file');
    if (!form.elements.extra_file.files.length) data.delete('extra_file');
    // Checkboxes submit nothing when unchecked — send an explicit boolean.
    data.set('visible', form.elements.visible.checked ? 'true' : 'false');

    if (typeSel.value !== 'docker') {
      for (const k of [
        'docker_cpu_limit', 'docker_memory_limit', 'docker_pids_limit',
        'docker_max_instances_per_player',
      ]) data.delete(k);

      btn.disabled = true;
      btn.textContent = 'Creating…';
      try {
        await uploadForm('POST', '/challenges', data);
        msg.classList.add('show', 'success');
        msg.textContent = 'Challenge created.';
        form.reset();
        reflectType();
      } catch (err) {
        msg.classList.add('show', 'error');
        msg.textContent = err.message || 'Create failed.';
      } finally {
        btn.disabled = false;
        btn.textContent = 'Create';
      }
      return;
    }

    // Docker → open the build console and stream ndjson lines.
    btn.disabled = true;
    btn.textContent = 'Building…';
    try {
      await runStreamingBuild(data);
      msg.classList.add('show', 'success');
      msg.textContent = 'Challenge created.';
      form.reset();
      reflectType();
    } catch (err) {
      msg.classList.add('show', 'error');
      msg.textContent = err.message || 'Build failed.';
    } finally {
      btn.disabled = false;
      btn.textContent = 'Create';
    }
  });
}

// Live build console (docker challenges only)

async function runStreamingBuild(formData) {
  const overlay = el('div', { class: 'modal-overlay' });
  const modal   = el('div', { class: 'modal panel build-console', style: 'max-width:760px;width:100%;' });
  modal.innerHTML = `
    <h3>Building docker image</h3>
    <div class="build-log mono" id="build-log"></div>
    <div style="display:flex;justify-content:flex-end;gap:.5rem;margin-top:.6rem;">
      <button class="btn btn-ghost" type="button" id="build-close" disabled>Close</button>
    </div>
  `;
  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  const logBox    = modal.querySelector('#build-log');
  const closeBtn  = modal.querySelector('#build-close');
  closeBtn.addEventListener('click', () => overlay.remove());

  const append = (line, kind) => {
    const row = document.createElement('div');
    row.className = 'build-line' + (kind ? ' kind-' + kind : '');
    row.textContent = line;
    logBox.appendChild(row);
    logBox.scrollTop = logBox.scrollHeight;
  };

  const res = await fetch('/scenario/challenges/stream', {
    method:  'POST',
    headers: state.token ? { Authorization: 'Bearer ' + state.token } : {},
    body:    formData,
  });

  if (!res.ok) {
    const text = await res.text();
    append('HTTP ' + res.status + ': ' + text, 'error');
    closeBtn.disabled = false;
    throw new Error('Build request failed: ' + res.status);
  }

  const reader  = res.body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer    = '';
  let errored   = false;
  let succeeded = false;

  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, nl);
        buffer = buffer.slice(nl + 1);
        if (!line.trim()) continue;
        let ev;
        try { ev = JSON.parse(line); } catch { append(line); continue; }
        if (ev.phase === 'building' && ev.stream) {
          append(ev.stream.replace(/\n$/, ''), 'build');
        } else if (ev.phase === 'building' && ev.error) {
          append('ERROR: ' + ev.error, 'error');
        } else if (ev.phase === 'error') {
          append('ERROR: ' + ev.message, 'error');
          errored = true;
        } else if (ev.phase === 'complete') {
          append('✓ Challenge created.', 'success');
          succeeded = true;
        } else if (ev.message) {
          append('· ' + ev.message, 'info');
        }
      }
    }
  } finally {
    closeBtn.disabled = false;
  }

  if (errored) throw new Error('Build failed (see console).');
  if (!succeeded) throw new Error('Build ended unexpectedly.');

  // Refresh caches so the new challenge appears in the list tab.
  await Promise.all([refreshSolved(), refreshMySessions()]);
}

// Live sessions tab (admin / instructor)

async function renderSessionsTab(container) {
  container.innerHTML = '<div class="empty"><span class="spinner"></span> loading…</div>';

  // Map challenge_id → title so we can show names, not UUIDs.
  let challenges = [];
  try { challenges = await scenario('GET', '/challenges', { auth: true }); }
  catch { challenges = []; }
  const titleById = new Map(challenges.map((c) => [c.id, c.title]));

  // Source from /sessions (the platform's own view — staff see every player's
  // session, and each row carries the session id needed to stop it).
  let sessions = [];
  try {
    sessions = await scenario('GET', '/sessions', { auth: true });
  } catch (err) {
    container.innerHTML = `<div class="empty">Failed to load sessions: ${escapeHtml(err.message)}</div>`;
    return;
  }
  const running = (sessions || []).filter((s) => s.status === 'running');

  container.innerHTML = '';
  container.appendChild(el('div', { class: 'section-head' },
    el('h2', {}, 'Live sessions'),
    el('span', { class: 'hint' }, `${running.length} running`),
  ));

  if (!running.length) {
    container.appendChild(el('div', { class: 'empty' }, 'No running player instances.'));
    return;
  }

  const wrap = el('div', { class: 'table-wrap' });
  wrap.innerHTML = `
    <table>
      <thead><tr>
        <th>Challenge</th>
        <th>Player</th>
        <th>Endpoint</th>
        <th>State</th>
        <th style="text-align:right;">Action</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  `;
  const body = wrap.querySelector('tbody');
  running.forEach((s) => {
    const title = titleById.get(s.challenge_id) || '—';
    // Strip the container-internal port — just show host:port.
    const hostPort = Object.values(s.ports || {}).find(Boolean) || '';
    const endpoint = hostPort ? `${s.hostname || 'localhost'}:${hostPort}` : '';
    const tr = el('tr');
    tr.innerHTML = `
      <td><strong>${escapeHtml(title)}</strong></td>
      <td>${escapeHtml(s.username || '—')}</td>
      <td class="mono" style="font-size:.85rem;">${endpoint ? escapeHtml(endpoint) : '<span class="badge muted">no ports</span>'}</td>
      <td><span class="badge ok">${escapeHtml(s.status)}</span></td>
      <td style="text-align:right;"></td>
    `;
    const stopBtn = el('button', { class: 'btn btn-danger btn-sm', type: 'button' }, 'Stop instance');
    stopBtn.addEventListener('click', () => stopInstance(s, title, container));
    tr.lastElementChild.appendChild(stopBtn);
    body.appendChild(tr);
  });
  container.appendChild(wrap);
}

// Staff: terminate a specific player's running instance (point 2). Confirmed
// via the modal system — no native dialogs.
async function stopInstance(session, title, container) {
  const ok = await modalConfirm({
    title: 'Stop player instance',
    message: `Stop ${session.username || 'this player'}'s running instance of "${title}"?\nTheir container will be torn down immediately.`,
    variant: 'danger', confirmText: 'Stop instance',
  });
  if (!ok) return;
  try {
    // Staff teardown also goes through Orchestration so the full sequence is
    // reversed (observation, attempt, access) — not just the container.
    await orchestration('DELETE', `/sessions/${encodeURIComponent(session.id)}`, { auth: true });
    // Keep the per-card cache in sync if this was the current user's own session.
    cache.mySessions.delete(session.challenge_id);
    await renderSessionsTab(container);
  } catch (err) {
    await modalAlert({ title: 'Stop failed', message: err.message || 'Could not stop the instance.', variant: 'danger' });
  }
}

// Editor: edit modal

function openEditModal(c, listContainer, onChanged = null) {
  const rerender = onChanged || (() => renderListTab(listContainer));
  const overlay = el('div', { class: 'modal-overlay' });
  const modal   = el('div', { class: 'modal panel', style: 'max-width:560px;' });
  const docker  = isDocker(c);
  const lab     = isLab(c);
  const d       = c.docker || {};

  const currentType = (c.challenge_type || (docker ? 'docker' : 'static')).toLowerCase();
  const currentCategory = c.category || 'misc';

  modal.innerHTML = `
    <h3>Edit challenge${docker ? ' (docker)' : ''}</h3>
    <form id="ch-edit" enctype="multipart/form-data">
      <div class="ch-form-grid">
        <div class="field"><label>Title</label>
          <input name="title" value="${escapeHtml(c.title)}" required /></div>
        <div class="field"><label>Type</label>
          <select name="challenge_type">
            ${TYPES.map(t => `<option value="${t}" ${t === currentType ? 'selected' : ''}>${t}</option>`).join('')}
          </select></div>
        <div class="field"><label>Category</label>
          <input name="category" value="${escapeHtml(currentCategory)}" /></div>
        <div class="field"><label>Difficulty</label>
          <select name="difficulty">
            ${DIFFICULTIES.map(x => `<option value="${x}" ${x === c.difficulty ? 'selected' : ''}>${x}</option>`).join('')}
          </select></div>
        <div class="field"><label>Points</label>
          <input name="points" type="number" min="0" value="${c.points}" /></div>
        <div class="field"><label>Max attempts (0 = unlimited)</label>
          <input name="max_attempts" type="number" min="0" value="${c.max_attempts}" /></div>
        <div class="field span-2"><label>Tags (comma-separated)</label>
          <input name="tags" value="${escapeHtml((c.tags || []).join(', '))}" /></div>
        <div class="field span-2"><label>Description</label>
          <textarea name="description" rows="3">${escapeHtml(c.description || '')}</textarea></div>
        <div class="field"><label>${isAdmin() ? 'Flag' : 'New flag (leave blank to keep)'}</label>
          <input name="flag" id="ch-edit-flag" placeholder="flag{...}" autocomplete="off" />
          ${isAdmin() ? '<small class="hint" id="ch-flag-hint">loading current flag…</small>' : ''}</div>
        <div class="field"><label>${docker ? 'Replace build context (.tar) — leave blank to keep'
                                      : lab ? 'Replace compose (.yml) — leave blank to keep'
                                      : 'Replace file (optional)'}</label>
          <input name="file" type="file" /></div>
        ${(docker || lab) ? `
          <div class="field span-2"><label>Replace downloadable file (optional)</label>
            <input name="extra_file" type="file" /></div>` : ''}
      </div>

      ${docker ? `
        <fieldset style="border:1px dashed var(--border);border-radius:8px;padding:.75rem 1rem;margin-top:.5rem;">
          <legend class="mono" style="padding:0 .4rem;color:var(--fg-dim);font-size:.7rem;letter-spacing:.18em;">DOCKER RUNTIME</legend>
          <div class="ch-form-grid">
            <div class="field"><label>CPU limit</label>
              <input name="docker_cpu_limit" type="number" min="0.1" step="0.1" value="${d.cpu_limit ?? 1}" /></div>
            <div class="field"><label>Memory limit</label>
              <input name="docker_memory_limit" value="${escapeHtml(d.memory_limit || '512m')}" /></div>
            <div class="field"><label>PIDs limit</label>
              <input name="docker_pids_limit" type="number" min="50" value="${d.pids_limit ?? 200}" /></div>
            <div class="field"><label>Max instances / player</label>
              <input name="docker_max_instances_per_player" type="number" min="1" value="${d.max_instances_per_player ?? 1}" /></div>
          </div>
        </fieldset>
      ` : ''}

      <div style="display:flex;gap:.5rem;justify-content:flex-end;margin-top:1rem;">
        <button class="btn btn-ghost" type="button" id="ch-edit-cancel">Cancel</button>
        <button class="btn"           type="submit" id="ch-edit-save">Save</button>
      </div>
      <div id="ch-edit-msg" class="msg" style="margin-top:.5rem;"></div>
    </form>
  `;
  overlay.appendChild(modal);
  document.body.appendChild(overlay);

  // Admin-only: reveal the actual current flag in the visible field (point 5).
  // Instructors keep the blank "new flag" field — the endpoint is admin-only.
  if (isAdmin()) {
    const flagInput = modal.querySelector('#ch-edit-flag');
    const flagHint  = modal.querySelector('#ch-flag-hint');
    scenario('GET', `/challenges/${encodeURIComponent(c.id)}/flag`, { auth: true })
      .then((res) => {
        if (res && res.recoverable && res.flag) {
          flagInput.value = res.flag;
          if (flagHint) flagHint.textContent = 'Current flag shown — edit to change it.';
        } else if (flagHint) {
          flagHint.textContent = 'Original flag not recoverable (predates flag storage) — type a new one to replace it.';
        }
      })
      .catch(() => { if (flagHint) flagHint.textContent = 'Could not load the current flag.'; });
  }

  modal.querySelector('#ch-edit-cancel').addEventListener('click', () => overlay.remove());
  overlay.addEventListener('click', (ev) => { if (ev.target === overlay) overlay.remove(); });

  modal.querySelector('#ch-edit').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const form = ev.currentTarget;
    const msg  = modal.querySelector('#ch-edit-msg');
    const btn  = modal.querySelector('#ch-edit-save');

    msg.classList.remove('show', 'error', 'success');
    btn.disabled = true;
    btn.textContent = 'Saving…';

    const data = new FormData();
    for (const [name, raw] of new FormData(form).entries()) {
      if (name === 'file' || name === 'extra_file') continue;
      if (name === 'flag' && !raw.toString().trim()) continue;
      data.append(name, raw);
    }
    if (form.elements.file && form.elements.file.files.length) {
      data.append('file', form.elements.file.files[0]);
    }
    if (form.elements.extra_file && form.elements.extra_file.files.length) {
      data.append('extra_file', form.elements.extra_file.files[0]);
    }

    try {
      await uploadForm('PUT', `/challenges/${encodeURIComponent(c.id)}`, data);
      overlay.remove();
      await rerender();
    } catch (err) {
      msg.classList.add('show', 'error');
      msg.textContent = err.message || 'Save failed.';
      btn.disabled = false;
      btn.textContent = 'Save';
    }
  });
}
