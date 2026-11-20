import { state, hasAnyRole, isAdmin } from './state.js';
import { iam, scenario, learning, data as dataApi, local } from './http.js';
import { el, escapeHtml } from './dom.js';

const DIFF_ORDER = { easy: 0, medium: 1, hard: 2 };

// Resolve a request, swallowing errors so one dead service can't blank the
// whole dashboard. Each panel degrades to an empty/placeholder state instead.
async function safe(fn, fallback) {
  try { return await fn(); } catch { return fallback; }
}

export async function renderDashboard(outlet) {
  const name = state.me?.display_name || state.me?.username || 'operator';
  outlet.appendChild(el('div', { class: 'page-head' },
    el('h1', {}, headline()),
    el('p',  {}, `${subtitle()} · signed in as ${name}`),
  ));

  const host = el('div', { id: 'dash-host' });
  outlet.appendChild(host);
  host.innerHTML = '<div class="empty"><span class="spinner"></span> loading…</div>';

  if (isAdmin())                 return renderAdminDashboard(host);
  if (hasAnyRole('instructor'))  return renderInstructorDashboard(host);
  return renderPlayerDashboard(host);
}

function headline() {
  if (isAdmin())                return 'Platform operations';
  if (hasAnyRole('instructor')) return 'Cohort operations';
  return 'Operator console';
}

function subtitle() {
  if (isAdmin())                return 'Service health, active sessions and learner performance';
  if (hasAnyRole('instructor')) return 'Active sessions, challenge completion and players needing attention';
  return 'Progress, next objective and standing';
}

// Shared building blocks

function statRow(items) {
  const stats = el('div', { class: 'stats' });
  stats.innerHTML = items.map((i) => `
    <div class="stat">
      <div class="label">${escapeHtml(i.label)}</div>
      <div class="value ${i.small ? 'small' : ''}">${i.value}</div>
      ${i.sub ? `<div class="hint" style="margin-top:.25rem;">${i.sub}</div>` : ''}
    </div>`).join('');
  return stats;
}

function sectionHead(title, hint) {
  return el('div', { class: 'section-head' },
    el('h2', {}, title),
    hint ? el('span', { class: 'hint' }, hint) : null,
  );
}

function panel(title, innerHTML) {
  const p = el('div', { class: 'panel' });
  p.innerHTML = `<h3>${escapeHtml(title)}</h3>${innerHTML}`;
  return p;
}

function bar(pct) {
  const v = Math.max(0, Math.min(100, Math.round(pct)));
  return `<div class="dash-bar"><span style="width:${v}%"></span></div>`;
}

function diffBadge(d) {
  return `<span class="badge role-${escapeHtml(d || 'easy')}">${escapeHtml(d || '—')}</span>`;
}

function emptyRow(cols, text) {
  return `<tr><td colspan="${cols}" class="empty">${escapeHtml(text)}</td></tr>`;
}

// Admin dashboard

async function renderAdminDashboard(host) {
  const [users, chalStats, sessions, board, catalog, recent] = await Promise.all([
    safe(() => iam('GET', '/users', { auth: true }), []),
    safe(() => scenario('GET', '/challenges/stats', { auth: true }), null),
    safe(() => scenario('GET', '/sessions', { auth: true }), []),
    safe(() => learning('GET', '/leaderboard?limit=500', { auth: true }), []),
    safe(() => local('GET', '/api/services'), null),
    safe(() => dataApi('GET', '/logs?limit=8&offset=0', { auth: true }), null),
  ]);

  const byRole = { player: 0, instructor: 0, admin: 0 };
  (users || []).forEach((u) => (u.roles || []).forEach((r) => { if (r in byRole) byRole[r] += 1; }));
  const running = (sessions || []).filter((s) => s.status === 'running');
  const avgScore = board.length
    ? Math.round(board.reduce((a, b) => a + b.total_score, 0) / board.length) : 0;
  const withAttempts = chalStats ? chalStats.challenges.filter((c) => c.attempts > 0) : [];
  const avgCompletion = withAttempts.length
    ? Math.round(100 * withAttempts.reduce((a, c) => a + c.completion_rate, 0) / withAttempts.length) : 0;

  host.innerHTML = '';
  host.appendChild(statRow([
    { label: 'Users', value: users.length,
      sub: `${byRole.player} players · ${byRole.instructor} instructors · ${byRole.admin} admins` },
    { label: 'Active sessions', value: running.length, sub: `${(sessions || []).length} total ever` },
    { label: 'Avg player score', value: avgScore, sub: `${board.length} on the board` },
    { label: 'Avg completion', value: avgCompletion + '%', sub: `${chalStats ? chalStats.total_challenges : 0} challenges` },
  ]));

  const grid = el('div', { class: 'dash-grid' });
  host.appendChild(grid);

  // System health.
  grid.appendChild(panel('System health', healthHTML(catalog, running.length)));

  // Top 5 most played.
  grid.appendChild(panel('Top 5 challenges', topChallengesHTML(chalStats)));

  // Player performance overview — leaderboard top.
  grid.appendChild(panel('Player performance', leaderboardHTML(board.slice(0, 6))));

  // Recent platform activity.
  grid.appendChild(panel('Recent activity', activityHTML(recent)));
}

function healthHTML(catalog, runningCount) {
  const known = (catalog && (catalog.services || catalog)) || {};
  const services = [
    'iam-service', 'scenario-service', 'events-service',
    'monitoring-service', 'data-logging-service', 'learning-service',
    'consumer-portal-service',
  ];
  if (!catalog) {
    return '<div class="empty">Service registry unreachable.</div>';
  }
  const rows = services.map((name) => {
    const online = Object.prototype.hasOwnProperty.call(known, name);
    return `<div class="health-row">
      <span class="health-dot ${online ? 'ok' : 'off'}"></span>
      <span class="mono" style="font-size:.8rem;">${escapeHtml(name)}</span>
      <span class="badge ${online ? 'ok' : 'muted'}" style="margin-left:auto;">${online ? 'online' : 'offline'}</span>
    </div>`;
  }).join('');
  const online = services.filter((n) => Object.prototype.hasOwnProperty.call(known, n)).length;
  return `<div class="hint" style="margin-bottom:.5rem;">${online}/${services.length} services online · ${runningCount} live container(s)</div>${rows}`;
}

function topChallengesHTML(chalStats) {
  if (!chalStats || !chalStats.challenges.length) {
    return '<div class="empty">No challenges yet.</div>';
  }
  const top = chalStats.challenges.slice(0, 5);
  return `<div class="table-wrap"><table>
    <thead><tr><th>Challenge</th><th>Diff</th><th style="text-align:right;">Plays</th><th style="text-align:right;">Solved</th></tr></thead>
    <tbody>${top.map((c) => `<tr>
      <td><strong>${escapeHtml(c.title)}</strong></td>
      <td>${diffBadge(c.difficulty)}</td>
      <td class="mono" style="text-align:right;">${c.attempts}</td>
      <td class="mono" style="text-align:right;">${c.solvers}</td>
    </tr>`).join('')}</tbody></table></div>`;
}

function leaderboardHTML(rows) {
  if (!rows.length) return '<div class="empty">No solves yet.</div>';
  return `<div class="table-wrap"><table>
    <thead><tr><th style="width:48px;">#</th><th>Player</th><th style="text-align:right;">Score</th><th style="text-align:right;">Solved</th></tr></thead>
    <tbody>${rows.map((r) => {
      const medal = r.rank === 1 ? '🥇' : r.rank === 2 ? '🥈' : r.rank === 3 ? '🥉' : `#${r.rank}`;
      return `<tr>
        <td class="mono">${medal}</td>
        <td><strong>${escapeHtml(r.username)}</strong>${r.team ? ` <span class="badge muted">${escapeHtml(r.team)}</span>` : ''}</td>
        <td class="mono" style="text-align:right;">${r.total_score}</td>
        <td class="mono" style="text-align:right;">${r.solve_count}</td>
      </tr>`;
    }).join('')}</tbody></table></div>`;
}

function activityHTML(page) {
  const items = (page && page.items) || [];
  if (!items.length) return '<div class="empty">No recorded activity yet.</div>';
  return `<ul class="feed">${items.map((r) => {
    const ts = new Date(r.timestamp);
    const when = isNaN(ts) ? '' : ts.toLocaleTimeString();
    return `<li>
      <span class="feed-key mono">${escapeHtml(r.routing_key || '—')}</span>
      <span class="feed-svc">${escapeHtml(r.service || '')}</span>
      <span class="feed-when mono">${escapeHtml(when)}</span>
    </li>`;
  }).join('')}</ul>`;
}

// Instructor dashboard

async function renderInstructorDashboard(host) {
  const [challenges, chalStats, sessions, board] = await Promise.all([
    safe(() => scenario('GET', '/challenges', { auth: true }), []),
    safe(() => scenario('GET', '/challenges/stats', { auth: true }), null),
    safe(() => scenario('GET', '/sessions', { auth: true }), []),
    safe(() => learning('GET', '/leaderboard?limit=500', { auth: true }), []),
  ]);
  const titleById = new Map((challenges || []).map((c) => [c.id, c.title]));
  const running = (sessions || []).filter((s) => s.status === 'running');
  // Players who have enrolled but not solved anything yet — need attention.
  const flagged = board.filter((p) => p.solve_count === 0);

  host.innerHTML = '';
  host.appendChild(statRow([
    { label: 'Active sessions', value: running.length, small: false },
    { label: 'Challenges', value: (challenges || []).length },
    { label: 'Players tracked', value: board.length },
    { label: 'Need attention', value: flagged.length, sub: 'no solves yet' },
  ]));

  const grid = el('div', { class: 'dash-grid' });
  host.appendChild(grid);

  // Live sessions with status.
  grid.appendChild(panel('Active sessions', sessionsHTML(running, titleById)));

  // Challenge completion rates.
  grid.appendChild(panel('Challenge completion', completionHTML(chalStats)));

  // Per-player progress.
  grid.appendChild(panel('Player progress', leaderboardHTML(board.slice(0, 8))));

  // Flagged players.
  grid.appendChild(panel('Needs attention', flaggedHTML(flagged)));
}

function sessionsHTML(running, titleById) {
  if (!running.length) return '<div class="empty">No running player instances.</div>';
  return `<div class="table-wrap"><table>
    <thead><tr><th>Challenge</th><th>Player</th><th>Endpoint</th></tr></thead>
    <tbody>${running.map((s) => {
      const hostPort = Object.values(s.ports || {}).find(Boolean) || '';
      const endpoint = hostPort ? `${s.hostname || 'localhost'}:${hostPort}` : '—';
      return `<tr>
        <td><strong>${escapeHtml(titleById.get(s.challenge_id) || '—')}</strong></td>
        <td>${escapeHtml(s.username || '—')}</td>
        <td class="mono" style="font-size:.8rem;">${escapeHtml(endpoint)}</td>
      </tr>`;
    }).join('')}</tbody></table></div>
    <div class="hint" style="margin-top:.5rem;">Stop a player's instance from the Challenges → Live sessions tab.</div>`;
}

function completionHTML(chalStats) {
  if (!chalStats || !chalStats.challenges.length) return '<div class="empty">No challenges yet.</div>';
  const rows = [...chalStats.challenges].sort((a, b) => a.completion_rate - b.completion_rate).slice(0, 8);
  return rows.map((c) => `
    <div class="dash-prog">
      <div class="dash-prog-top">
        <span>${escapeHtml(c.title)} ${diffBadge(c.difficulty)}</span>
        <span class="mono">${Math.round(c.completion_rate * 100)}%</span>
      </div>
      ${bar(c.completion_rate * 100)}
      <div class="hint">${c.solvers}/${c.attempters || 0} solved · ${c.attempts} attempt(s)</div>
    </div>`).join('');
}

function flaggedHTML(flagged) {
  if (!flagged.length) return '<div class="empty">Everyone has solved at least one challenge. 🎉</div>';
  return `<ul class="feed">${flagged.slice(0, 12).map((p) => `<li>
    <span class="feed-key">${escapeHtml(p.username)}</span>
    ${p.team ? `<span class="badge muted">${escapeHtml(p.team)}</span>` : ''}
    <span class="feed-when badge bad" style="margin-left:auto;">0 solves</span>
  </li>`).join('')}</ul>`;
}

// Player dashboard

async function renderPlayerDashboard(host) {
  const [me, challenges, solvesResp] = await Promise.all([
    safe(() => learning('GET', '/me', { auth: true }), null),
    safe(() => scenario('GET', '/challenges', { auth: true }), []),
    safe(() => scenario('GET', '/my-solves', { auth: true }), { solved: [] }),
  ]);
  const solvedIds = new Set((solvesResp && solvesResp.solved) || []);
  const total = (challenges || []).length;
  const done = challenges.filter((c) => solvedIds.has(c.id)).length;
  const pct = total ? Math.round(100 * done / total) : 0;

  host.innerHTML = '';
  host.appendChild(statRow([
    { label: 'Score', value: me ? me.total_score : 0 },
    { label: 'Rank', value: me ? `#${me.rank}` : '—' },
    { label: 'Solved', value: `${done}/${total}`, sub: bar(pct) },
    { label: 'Team', value: me && me.team ? `<span class="badge muted">${escapeHtml(me.team)}</span>` : '<span class="hint">—</span>', small: true },
  ]));

  const grid = el('div', { class: 'dash-grid' });
  host.appendChild(grid);

  grid.appendChild(panel('Progress', progressHTML(done, total, pct)));
  grid.appendChild(panel('Recommended next', recommendHTML(challenges, solvedIds)));
  grid.appendChild(panel('Score history', historyHTML(me, challenges)));
  grid.appendChild(panel('Recent solves', recentSolvesHTML(me, challenges)));
}

function progressHTML(done, total, pct) {
  const remaining = Math.max(0, total - done);
  return `
    <div class="dash-prog">
      <div class="dash-prog-top"><span>Challenges completed</span><span class="mono">${done}/${total}</span></div>
      ${bar(pct)}
    </div>
    <div class="stats" style="margin-top:.75rem;">
      <div class="stat"><div class="label">Completed</div><div class="value">${done}</div></div>
      <div class="stat"><div class="label">Remaining</div><div class="value">${remaining}</div></div>
    </div>`;
}

function recommendHTML(challenges, solvedIds) {
  // Difficulty progression: easiest unsolved first, then by points.
  const unsolved = challenges
    .filter((c) => !solvedIds.has(c.id))
    .sort((a, b) =>
      (DIFF_ORDER[a.difficulty] ?? 1) - (DIFF_ORDER[b.difficulty] ?? 1)
      || (a.points || 0) - (b.points || 0));
  if (!unsolved.length) return '<div class="empty">You\'ve solved everything available. 🏆</div>';
  const picks = unsolved.slice(0, 4);
  return `<div class="hint" style="margin-bottom:.5rem;">Next steps on your difficulty ladder.</div>
    ${picks.map((c) => `<div class="rec-row">
      <div>
        <strong>${escapeHtml(c.title)}</strong>
        <div class="hint">${escapeHtml(c.category || 'misc')} · ${c.points} pts</div>
      </div>
      ${diffBadge(c.difficulty)}
    </div>`).join('')}
    <div style="margin-top:.6rem;"><a class="btn btn-ghost btn-sm" href="#/challenges">Go to challenges</a></div>`;
}

function cumulative(solves) {
  // Oldest → newest cumulative score.
  const asc = [...solves].sort((a, b) => new Date(a.solved_at) - new Date(b.solved_at));
  let running = 0;
  return asc.map((s) => (running += (s.points || 0)));
}

function sparkline(values, { w = 300, h = 56 } = {}) {
  if (!values.length) return '';
  const max = Math.max(...values, 1);
  const stepX = values.length > 1 ? w / (values.length - 1) : 0;
  const pts = values.map((v, i) =>
    `${(i * stepX).toFixed(1)},${(h - (v / max) * (h - 6) - 3).toFixed(1)}`).join(' ');
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" width="100%" height="${h}">
    <polyline fill="none" stroke="var(--ok)" stroke-width="2" points="${pts}" />
  </svg>`;
}

function historyHTML(me, challenges) {
  const solves = (me && me.solves) || [];
  if (!solves.length) return '<div class="empty">No solves yet — your score history starts with your first flag.</div>';
  const series = cumulative(solves);
  const totalPts = series[series.length - 1];
  return `${sparkline(series)}
    <div class="hint" style="margin-top:.4rem;">${solves.length} solve(s) · ${totalPts} points earned over time</div>`;
}

function recentSolvesHTML(me, challenges) {
  const titleById = new Map((challenges || []).map((c) => [c.id, c.title]));
  const solves = [...((me && me.solves) || [])]
    .sort((a, b) => new Date(b.solved_at) - new Date(a.solved_at))
    .slice(0, 6);
  if (!solves.length) return '<div class="empty">No solves yet.</div>';
  return `<div class="table-wrap"><table>
    <thead><tr><th>Challenge</th><th>Diff</th><th style="text-align:right;">Points</th></tr></thead>
    <tbody>${solves.map((s) => `<tr>
      <td>${escapeHtml(titleById.get(s.challenge_id) || (s.challenge_id ? s.challenge_id.slice(0, 8) + '…' : '—'))}</td>
      <td>${diffBadge(s.difficulty)}</td>
      <td class="mono" style="text-align:right;">+${s.points}</td>
    </tr>`).join('')}</tbody></table></div>`;
}
