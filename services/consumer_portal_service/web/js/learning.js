import { state, hasAnyRole } from './state.js';
import { learning as learnApi, scenario } from './http.js';
import { el, escapeHtml } from './dom.js';
import { modalPrompt, modalAlert } from './modal.js';
import { showPlayerSolves } from './teams.js';

// Resolve challenge id → title so solved-challenge lists show names, not IDs.
async function challengeTitleMap() {
  try {
    const cs = await scenario('GET', '/challenges', { auth: true });
    return new Map((cs || []).map((c) => [c.id, c.title]));
  } catch {
    return new Map();
  }
}

const STAFF_ROLES = ['admin', 'instructor'];
const isStaff = () => hasAnyRole(...STAFF_ROLES);

export async function renderScoreboard(outlet) {
  outlet.appendChild(el('div', { class: 'page-head' },
    el('h1', {}, 'Scoreboard'),
    el('p',  {}, 'Points are awarded by difficulty when you solve a challenge.'),
  ));

  const mineHost  = el('div', { id: 'lp-mine' });
  const boardHost = el('div', { id: 'lp-board', style: 'margin-top:1rem;' });
  const teamHost  = el('div', { id: 'lp-teams', style: 'margin-top:1rem;' });
  outlet.appendChild(mineHost);
  outlet.appendChild(boardHost);
  outlet.appendChild(teamHost);

  const titleById = await challengeTitleMap();
  await Promise.all([refreshMine(mineHost, titleById), refreshBoard(boardHost),
                     refreshTeamBoard(teamHost)]);
}

// My standing

async function refreshMine(host, titleById = new Map()) {
  host.innerHTML = '<div class="empty"><span class="spinner"></span> loading…</div>';
  let me;
  try {
    me = await learnApi('GET', '/me', { auth: true });
  } catch (err) {
    host.innerHTML = `<div class="empty">Failed to load: ${escapeHtml(err.message)}</div>`;
    return;
  }

  const stats = el('div', { class: 'stats' });
  stats.innerHTML = `
    <div class="stat"><div class="label">Your score</div><div class="value">${me.total_score}</div></div>
    <div class="stat"><div class="label">Rank</div><div class="value">#${me.rank}</div></div>
    <div class="stat"><div class="label">Solved</div><div class="value">${me.solve_count}</div></div>
    <div class="stat"><div class="label">Team</div><div class="value small">${me.team ? escapeHtml(me.team) : '<span class="hint">—</span>'}</div></div>
  `;
  host.innerHTML = '';
  host.appendChild(stats);

  if (me.solves && me.solves.length) {
    host.appendChild(el('div', { class: 'section-head' }, el('h2', {}, 'Your solves')));
    const wrap = el('div', { class: 'table-wrap' });
    wrap.innerHTML = `
      <table>
        <thead><tr><th>Challenge</th><th>Difficulty</th><th>Points</th><th>When</th></tr></thead>
        <tbody>${me.solves.map((s) => {
          const when = new Date(s.solved_at);
          const whenStr = isNaN(when) ? s.solved_at : when.toLocaleString();
          const name = titleById.get(s.challenge_id)
            || (s.challenge_id ? s.challenge_id.slice(0, 8) + '…' : '—');
          return `<tr>
            <td>${escapeHtml(name)}</td>
            <td><span class="badge role-${escapeHtml(s.difficulty)}">${escapeHtml(s.difficulty || '—')}</span></td>
            <td class="mono">+${s.points}</td>
            <td class="mono" style="font-size:.78rem;">${escapeHtml(whenStr)}</td>
          </tr>`;
        }).join('')}</tbody>
      </table>`;
    host.appendChild(wrap);
  }
}

// Leaderboard

async function refreshBoard(host) {
  host.innerHTML = '<div class="empty"><span class="spinner"></span> loading…</div>';

  let board, policyHint = '';
  try {
    board = await learnApi('GET', '/leaderboard?limit=100', { auth: true });
  } catch (err) {
    host.innerHTML = `<div class="empty">Failed to load leaderboard: ${escapeHtml(err.message)}</div>`;
    return;
  }

  if (isStaff()) {
    try {
      const st = await learnApi('GET', '/stats', { auth: true });
      const p = st.points_policy;
      policyHint = `easy ${p.easy} · medium ${p.medium} · hard ${p.hard} · `
                 + `${st.participants} players · ${st.total_solves} solves · ${st.total_points} pts`;
    } catch { /* non-fatal */ }
  }

  host.innerHTML = '';
  host.appendChild(el('div', { class: 'section-head' },
    el('h2', {}, 'Solo scoreboard'),
    el('span', { class: 'hint' }, policyHint),
  ));

  if (!board.length) {
    host.appendChild(el('div', { class: 'empty' }, 'No solves yet — be the first on the board.'));
    return;
  }

  const staff = isStaff();
  const wrap = el('div', { class: 'table-wrap' });
  wrap.innerHTML = `
    <table>
      <thead><tr>
        <th style="width:60px;">Rank</th>
        <th>Player</th>
        <th>Team</th>
        <th style="text-align:right;">Score</th>
        <th style="text-align:right;">Solved</th>
        ${staff ? '<th></th>' : ''}
      </tr></thead>
      <tbody></tbody>
    </table>`;
  const body = wrap.querySelector('tbody');
  board.forEach((r) => {
    const isMe = r.user_id === state.me.id;
    const medal = r.rank === 1 ? '🥇' : r.rank === 2 ? '🥈' : r.rank === 3 ? '🥉' : `#${r.rank}`;
    const teamCell = staff
      ? `<button class="btn btn-ghost btn-sm" data-team="${escapeHtml(r.user_id)}" data-current="${escapeHtml(r.team)}">${r.team ? escapeHtml(r.team) : 'set team'}</button>`
      : (r.team ? escapeHtml(r.team) : '<span class="hint">—</span>');
    const tr = el('tr');
    if (isMe) tr.style.background = 'rgba(255,255,255,0.04)';
    tr.innerHTML = `
      <td class="mono">${medal}</td>
      <td><a href="#/profile?u=${escapeHtml(r.user_id)}"><strong>${escapeHtml(r.username)}</strong></a>${isMe ? ' <span class="badge muted">you</span>' : ''}</td>
      <td>${teamCell}</td>
      <td class="mono" style="text-align:right;">${r.total_score}</td>
      <td class="mono" style="text-align:right;">${r.solve_count}</td>
      ${staff ? `<td style="text-align:right;"><button class="btn btn-ghost btn-sm" data-solves="${escapeHtml(r.user_id)}" data-username="${escapeHtml(r.username)}">Solves</button></td>` : ''}
    `;
    body.appendChild(tr);
  });
  host.appendChild(wrap);

  // Staff: assign teams (F07).
  body.querySelectorAll('[data-team]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const team = await modalPrompt({
        title: 'Assign team',
        label: 'Team name (blank to clear)',
        value: btn.dataset.current || '',
        placeholder: 'blue',
      });
      if (team === null) return;
      try {
        await learnApi('PUT', '/participants/' + encodeURIComponent(btn.dataset.team) + '/team',
          { auth: true, body: { team: team.trim() } });
        await refreshBoard(host);
      } catch (err) {
        await modalAlert({ title: 'Assign failed', message: err.message || 'Could not assign team.', variant: 'danger' });
      }
    });
  });

  // Staff: inspect a player's solves and revoke them (points come back off).
  body.querySelectorAll('[data-solves]').forEach((btn) => {
    btn.addEventListener('click', () =>
      showPlayerSolves(btn.dataset.solves, btn.dataset.username,
        () => refreshBoard(host)));
  });
}

// Team scoreboard (separate from the solo board)

async function refreshTeamBoard(host) {
  host.innerHTML = '<div class="empty"><span class="spinner"></span> loading…</div>';
  let board;
  try {
    board = await learnApi('GET', '/leaderboard/teams', { auth: true });
  } catch (err) {
    host.innerHTML = `<div class="empty">Failed to load team scoreboard: ${escapeHtml(err.message)}</div>`;
    return;
  }

  host.innerHTML = '';
  host.appendChild(el('div', { class: 'section-head' },
    el('h2', {}, 'Team scoreboard'),
    el('span', { class: 'hint' }, 'each challenge counts once per team'),
  ));

  if (!board.length) {
    host.appendChild(el('div', { class: 'empty' }, 'No teams yet.'));
    return;
  }

  const wrap = el('div', { class: 'table-wrap' });
  wrap.innerHTML = `
    <table>
      <thead><tr>
        <th style="width:60px;">Rank</th>
        <th>Team</th>
        <th>Motto</th>
        <th style="text-align:right;">Players</th>
        <th style="text-align:right;">Score</th>
        <th style="text-align:right;">Solved</th>
      </tr></thead>
      <tbody>${board.map((r) => {
        const medal = r.rank === 1 ? '🥇' : r.rank === 2 ? '🥈' : r.rank === 3 ? '🥉' : `#${r.rank}`;
        return `<tr>
          <td class="mono">${medal}</td>
          <td><strong>${escapeHtml(r.team)}</strong></td>
          <td class="hint">${r.motto ? escapeHtml(r.motto) : '—'}</td>
          <td class="mono" style="text-align:right;">${r.member_count}</td>
          <td class="mono" style="text-align:right;">${r.total_score}</td>
          <td class="mono" style="text-align:right;">${r.solve_count}</td>
        </tr>`;
      }).join('')}</tbody>
    </table>`;
  host.appendChild(wrap);
}
