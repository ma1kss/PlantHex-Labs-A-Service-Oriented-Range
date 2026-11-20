import { state, hasAnyRole } from './state.js';
import { learning as learnApi, iam, scenario } from './http.js';
import { el, escapeHtml } from './dom.js';
import { modalAlert, modalConfirm, modalPrompt, openModal } from './modal.js';

const isStaff = () => hasAnyRole('admin', 'instructor');

async function safe(fn, fallback) {
  try { return await fn(); } catch { return fallback; }
}

async function challengeTitleMap() {
  const cs = await safe(() => scenario('GET', '/challenges', { auth: true }), []);
  return new Map((cs || []).map((c) => [c.id, c.title]));
}

// After a learning-side revoke, also clear the scenario solved-state so the
// challenge shows as unsolved for those players again.
async function clearSubmissions(challengeId, userIds) {
  for (const uid of userIds) {
    await safe(() => scenario('DELETE',
      `/submissions?challenge_id=${encodeURIComponent(challengeId)}&user_id=${encodeURIComponent(uid)}`,
      { auth: true }), null);
  }
}

export async function renderTeams(outlet) {
  outlet.appendChild(el('div', { class: 'page-head' },
    el('h1', {}, 'Teams'),
    el('p', {}, isStaff()
      ? 'Create teams, place trainees on them, and manage team solves.'
      : 'The teams competing on the range.'),
  ));

  const host = el('div', { id: 'teams-host' });

  if (isStaff()) {
    const form = el('div', { class: 'panel', style: 'margin-bottom:1rem;' });
    form.innerHTML = `
      <h3>Create team</h3>
      <form id="team-form" autocomplete="off">
        <div class="ch-form-grid">
          <div class="field"><label>Team name</label>
            <input name="name" minlength="2" maxlength="64" placeholder="blue-team" required /></div>
          <div class="field"><label>Motto (optional)</label>
            <input name="motto" maxlength="255" placeholder="we pwn responsibly" /></div>
        </div>
        <div style="margin-top:.5rem;"><button class="btn" type="submit">Create team</button></div>
      </form>
    `;
    form.querySelector('#team-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const f = ev.currentTarget;
      try {
        await learnApi('POST', '/teams', { auth: true, body: {
          name: f.elements.name.value.trim(), motto: f.elements.motto.value.trim() } });
        f.reset();
        await refreshTeams(host);
      } catch (err) {
        await modalAlert({ title: 'Create failed', message: err.message, variant: 'danger' });
      }
    });
    outlet.appendChild(form);
  }

  outlet.appendChild(host);
  await refreshTeams(host);
}

async function refreshTeams(host) {
  host.innerHTML = '<div class="empty"><span class="spinner"></span> loading…</div>';
  let teams, board;
  try {
    [teams, board] = await Promise.all([
      learnApi('GET', '/teams', { auth: true }),
      safe(() => learnApi('GET', '/leaderboard/teams', { auth: true }), []),
    ]);
  } catch (err) {
    host.innerHTML = `<div class="empty">Failed to load teams: ${escapeHtml(err.message)}</div>`;
    return;
  }

  host.innerHTML = '';
  if (!teams.length) {
    host.appendChild(el('div', { class: 'empty' },
      isStaff() ? 'No teams yet — create the first one above.' : 'No teams yet.'));
    return;
  }

  const scoreByName = new Map((board || []).map((r) => [r.team, r]));
  const staff = isStaff();

  for (const t of teams) {
    const sc = scoreByName.get(t.name);
    const card = el('div', { class: 'panel', style: 'margin-bottom:1rem;' });
    const members = (t.members || []).map((m) => `
      <tr>
        <td><a href="#/profile?u=${escapeHtml(m.user_id)}"><strong>${escapeHtml(m.username || m.user_id.slice(0, 8))}</strong></a></td>
        <td class="mono" style="text-align:right;">${m.total_score}</td>
        <td class="mono" style="text-align:right;">${m.solve_count}</td>
        ${staff ? `<td style="text-align:right;"><button class="btn btn-ghost btn-sm" data-remove="${escapeHtml(m.user_id)}" data-username="${escapeHtml(m.username)}">Remove</button></td>` : ''}
      </tr>`).join('');

    card.innerHTML = `
      <div style="display:flex;gap:.8rem;align-items:baseline;flex-wrap:wrap;">
        <h3 style="margin:0;">${escapeHtml(t.name)}</h3>
        <span class="hint">${t.motto ? '“' + escapeHtml(t.motto) + '”' : 'no motto'}</span>
        <span class="spacer" style="flex:1;"></span>
        <span class="badge muted">${sc ? sc.total_score : 0} pts · ${sc ? sc.solve_count : 0} solves</span>
        ${staff ? `
          <button class="btn btn-ghost btn-sm" data-add>Add player</button>
          <button class="btn btn-ghost btn-sm" data-solves>Solves</button>
          <button class="btn btn-ghost btn-sm" data-motto>Edit motto</button>
          <button class="btn btn-danger btn-sm" data-delete>Delete</button>` : ''}
      </div>
      <div class="table-wrap" style="margin-top:.6rem;">
        <table>
          <thead><tr><th>Player</th><th style="text-align:right;">Score</th><th style="text-align:right;">Solved</th>${staff ? '<th></th>' : ''}</tr></thead>
          <tbody>${members || `<tr><td colspan="4" class="empty">No players yet.</td></tr>`}</tbody>
        </table>
      </div>
    `;

    if (staff) wireTeamActions(card, t, host);
    host.appendChild(card);
  }
}

function wireTeamActions(card, team, host) {
  card.querySelector('[data-add]').addEventListener('click', () => addPlayer(team, host));

  card.querySelector('[data-motto]').addEventListener('click', async () => {
    const motto = await modalPrompt({ title: `Motto for ${team.name}`,
      label: 'Motto (blank to clear)', value: team.motto || '' });
    if (motto === null) return;
    try {
      await learnApi('PUT', '/teams/' + encodeURIComponent(team.id),
        { auth: true, body: { motto: motto.trim() } });
      await refreshTeams(host);
    } catch (err) {
      await modalAlert({ title: 'Update failed', message: err.message, variant: 'danger' });
    }
  });

  card.querySelector('[data-delete]').addEventListener('click', async () => {
    const ok = await modalConfirm({ title: 'Delete team',
      message: `Delete team "${team.name}"? Its players stay, but leave the team.`,
      variant: 'danger', confirmText: 'Delete' });
    if (!ok) return;
    try {
      await learnApi('DELETE', '/teams/' + encodeURIComponent(team.id), { auth: true });
      await refreshTeams(host);
    } catch (err) {
      await modalAlert({ title: 'Delete failed', message: err.message, variant: 'danger' });
    }
  });

  card.querySelector('[data-solves]').addEventListener('click', () =>
    showTeamSolves(team, host));

  card.querySelectorAll('[data-remove]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const ok = await modalConfirm({ title: 'Remove player',
        message: `Remove "${btn.dataset.username}" from ${team.name}?`, confirmText: 'Remove' });
      if (!ok) return;
      try {
        await learnApi('PUT', '/participants/' + encodeURIComponent(btn.dataset.remove) + '/team',
          { auth: true, body: { team: '' } });
        await refreshTeams(host);
      } catch (err) {
        await modalAlert({ title: 'Remove failed', message: err.message, variant: 'danger' });
      }
    });
  });
}

// Add player (staff): pick a player account, enrol it onto the team

async function addPlayer(team, host) {
  const users = await safe(() => iam('GET', '/users', { auth: true }), []);
  const players = (users || []).filter((u) => (u.roles || []).includes('player'));
  const onTeam = new Set((team.members || []).map((m) => m.user_id));
  const options = players.filter((u) => !onTeam.has(u.id));

  if (!options.length) {
    await modalAlert({ title: 'Add player', message: 'No eligible player accounts found.' });
    return;
  }

  const body = el('div');
  body.innerHTML = `
    <div class="field"><label>Player</label>
      <select id="add-player-select">
        ${options.map((u) => `<option value="${escapeHtml(u.id)}" data-username="${escapeHtml(u.username)}">${escapeHtml(u.username)}</option>`).join('')}
      </select>
    </div>`;
  const modal = openModal({
    title: `Add player to ${team.name}`,
    body,
    actions: [
      { label: 'Cancel', value: null, ghost: true },
      { label: 'Add', value: 'add', autofocus: true },
    ],
  });
  const choice = await modal;
  if (choice !== 'add') return;

  const sel = body.querySelector('#add-player-select');
  const opt = sel.selectedOptions[0];
  try {
    await learnApi('POST', '/participants', { auth: true, body: {
      user_id: sel.value, username: opt.dataset.username, team: team.name } });
    await refreshTeams(host);
  } catch (err) {
    await modalAlert({ title: 'Add failed', message: err.message, variant: 'danger' });
  }
}

// Solve revocation (staff)

async function showTeamSolves(team, host) {
  const [solves, titleById] = await Promise.all([
    safe(() => learnApi('GET', `/teams/${encodeURIComponent(team.id)}/solves`, { auth: true }), []),
    challengeTitleMap(),
  ]);
  const body = el('div');
  if (!solves.length) {
    body.innerHTML = '<p class="hint">This team has no solves.</p>';
  } else {
    body.innerHTML = `
      <div class="table-wrap"><table>
        <thead><tr><th>Challenge</th><th>Player</th><th>Pts</th><th></th></tr></thead>
        <tbody>${solves.map((s) => `
          <tr>
            <td>${escapeHtml(titleById.get(s.challenge_id) || s.challenge_id.slice(0, 8) + '…')}</td>
            <td>${escapeHtml(s.username)}</td>
            <td class="mono">+${s.points}</td>
            <td><button class="btn btn-danger btn-sm" data-revoke="${escapeHtml(s.challenge_id)}">Revoke (team)</button></td>
          </tr>`).join('')}</tbody>
      </table></div>
      <p class="hint">Revoking removes the challenge for <strong>every</strong> member of the team.</p>`;
  }
  const modal = openModal({
    title: `Solves — ${team.name}`,
    body,
    actions: [{ label: 'Close', value: null, ghost: true }],
  });

  body.querySelectorAll('[data-revoke]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        const res = await learnApi('POST', '/solves/revoke', { auth: true,
          body: { challenge_id: btn.dataset.revoke, team_id: team.id } });
        await clearSubmissions(btn.dataset.revoke, res.revoked_user_ids || []);
        modal.close(null);
        await refreshTeams(host);
      } catch (err) {
        btn.disabled = false;
        await modalAlert({ title: 'Revoke failed', message: err.message, variant: 'danger' });
      }
    });
  });

  await modal;
}

// Player-level solve list + revoke. Shared with the Scoreboard view.
export async function showPlayerSolves(userId, username, onChanged) {
  const [solves, titleById] = await Promise.all([
    safe(() => learnApi('GET', `/participants/${encodeURIComponent(userId)}/solves`, { auth: true }), []),
    challengeTitleMap(),
  ]);
  const body = el('div');
  if (!solves.length) {
    body.innerHTML = '<p class="hint">No solves yet.</p>';
  } else {
    body.innerHTML = `
      <div class="table-wrap"><table>
        <thead><tr><th>Challenge</th><th>Difficulty</th><th>Pts</th><th></th></tr></thead>
        <tbody>${solves.map((s) => `
          <tr>
            <td>${escapeHtml(titleById.get(s.challenge_id) || s.challenge_id.slice(0, 8) + '…')}</td>
            <td><span class="badge role-${escapeHtml(s.difficulty)}">${escapeHtml(s.difficulty || '—')}</span></td>
            <td class="mono">+${s.points}</td>
            <td><button class="btn btn-danger btn-sm" data-revoke="${escapeHtml(s.challenge_id)}">Revoke</button></td>
          </tr>`).join('')}</tbody>
      </table></div>`;
  }
  const modal = openModal({
    title: `Solves — ${username}`,
    body,
    actions: [{ label: 'Close', value: null, ghost: true }],
  });

  body.querySelectorAll('[data-revoke]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      try {
        const res = await learnApi('POST', '/solves/revoke', { auth: true,
          body: { challenge_id: btn.dataset.revoke, user_id: userId } });
        await clearSubmissions(btn.dataset.revoke, res.revoked_user_ids || []);
        modal.close(null);
        if (onChanged) await onChanged();
      } catch (err) {
        btn.disabled = false;
        await modalAlert({ title: 'Revoke failed', message: err.message, variant: 'danger' });
      }
    });
  });

  await modal;
}
