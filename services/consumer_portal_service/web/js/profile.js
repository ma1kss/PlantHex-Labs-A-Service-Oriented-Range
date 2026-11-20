import { state, hasAnyRole, isAdmin } from './state.js';
import { $, el, escapeHtml } from './dom.js';
import { iam, scenario, learning } from './http.js';
import { modalAlert } from './modal.js';

// A small fixed avatar library — preset glyphs, no file upload (simple+secure).
export const AVATARS = [
  '🐉', '🦊', '🐺', '🦅', '🐙', '🦈', '🐍', '🦂',
  '🦉', '👾', '🤖', '🥷', '💀', '🛡️', '⚡', '🔥',
];

// What to show in any avatar slot: the chosen preset glyph, else the initial.
export function avatarGlyph(p) {
  const av = (p && p.avatar) || '';
  if (av) return av;
  const name = (p && (p.display_name || p.username)) || '?';
  return name.trim()[0]?.toUpperCase() || '?';
}

async function safe(fn, fallback) {
  try { return await fn(); } catch { return fallback; }
}

export async function renderProfile(outlet) {
  // #/profile?u=<user_id> → someone else's profile (role-gated server-side:
  // players see players, instructors see instructors+players, admins see all).
  const uid = new URLSearchParams(location.hash.split('?')[1] || '').get('u');
  if (uid && state.me && uid !== state.me.id) {
    return renderPublicProfile(outlet, uid);
  }

  outlet.appendChild(el('div', { class: 'page-head' },
    el('h1', {}, 'Profile'),
    el('p',  {}, 'Your identity, your stats, your story on the range.'),
  ));

  const host = el('div', { id: 'profile-host' });
  outlet.appendChild(host);
  host.innerHTML = '<div class="empty"><span class="spinner"></span> loading…</div>';

  const profile = await safe(() => iam('GET', '/profile', { auth: true }), null);
  if (!profile) {
    host.innerHTML = '<div class="empty">Failed to load your profile.</div>';
    return;
  }
  // Personal standing — works for every role (zeros if no participant row).
  const me = await safe(() => learning('GET', '/me', { auth: true }), null);

  host.innerHTML = '';
  host.appendChild(buildHeader(profile, host));
  host.appendChild(await buildStats(profile, me));
}

// Another user's profile (read-only, role-gated)

async function renderPublicProfile(outlet, userId) {
  outlet.appendChild(el('div', { class: 'page-head' },
    el('h1', {}, 'Profile'),
    el('p',  {}, 'A fellow operator on the range.'),
  ));

  const host = el('div');
  outlet.appendChild(host);
  host.innerHTML = '<div class="empty"><span class="spinner"></span> loading…</div>';

  let profile;
  try {
    profile = await iam('GET', '/profiles/' + encodeURIComponent(userId), { auth: true });
  } catch (err) {
    host.innerHTML = `<div class="empty">${err.status === 403
      ? 'You are not allowed to view this profile.'
      : 'Failed to load profile: ' + escapeHtml(err.message)}</div>`;
    return;
  }

  const since = profile.created_at ? new Date(profile.created_at) : null;
  const sinceStr = since && !isNaN(since) ? since.toLocaleDateString() : '—';
  const roles = (profile.roles || []).map((r) =>
    `<span class="badge role-${escapeHtml(r)}">${escapeHtml(r)}</span>`).join(' ');

  const card = el('div', { class: 'panel profile-head' });
  card.innerHTML = `
    <div class="profile-id">
      <div class="profile-avatar">${escapeHtml(avatarGlyph(profile))}</div>
      <div class="profile-meta">
        <h2 class="profile-name">${escapeHtml(profile.display_name || profile.username)}</h2>
        <div class="profile-handle mono">@${escapeHtml(profile.username)}</div>
        <div class="profile-roles">${roles || ''}</div>
        <div class="hint" style="margin-top:.35rem;">Member since ${escapeHtml(sinceStr)}</div>
      </div>
    </div>
    <div class="profile-bio">${profile.bio ? escapeHtml(profile.bio) : '<span class="hint">No bio yet.</span>'}</div>
  `;
  host.innerHTML = '';
  host.appendChild(card);

  // Standing from the (already-visible-to-everyone) leaderboard.
  const board = await safe(() => learning('GET', '/leaderboard?limit=500', { auth: true }), []);
  const row = (board || []).find((r) => r.user_id === userId);
  host.appendChild(statRow([
    { label: 'Score', value: row ? row.total_score : 0 },
    { label: 'Rank', value: row ? `#${row.rank}` : '—' },
    { label: 'Solved', value: row ? row.solve_count : 0 },
    { label: 'Team', value: row && row.team ? `<span class="badge muted">${escapeHtml(row.team)}</span>` : '<span class="hint">—</span>', small: true },
  ]));
}

// Header (identity + inline editor)

function buildHeader(profile, host) {
  const card = el('div', { class: 'panel profile-head' });
  const since = profile.created_at ? new Date(profile.created_at) : null;
  const sinceStr = since && !isNaN(since) ? since.toLocaleDateString() : '—';
  const roles = (profile.roles || []).map((r) =>
    `<span class="badge role-${escapeHtml(r)}">${escapeHtml(r)}</span>`).join(' ');

  card.innerHTML = `
    <div class="profile-id">
      <div class="profile-avatar">${escapeHtml(avatarGlyph(profile))}</div>
      <div class="profile-meta">
        <h2 class="profile-name">${escapeHtml(profile.display_name || profile.username)}</h2>
        <div class="profile-handle mono">@${escapeHtml(profile.username)}</div>
        <div class="profile-roles">${roles || ''}</div>
        <div class="hint" style="margin-top:.35rem;">Member since ${escapeHtml(sinceStr)}${profile.email ? ' · ' + escapeHtml(profile.email) : ''}</div>
      </div>
      <button class="btn btn-ghost" type="button" id="profile-edit-btn">Edit profile</button>
    </div>
    <div class="profile-bio">${profile.bio ? escapeHtml(profile.bio) : '<span class="hint">No bio yet — tell the range who you are.</span>'}</div>
    <div id="profile-editor"></div>
  `;

  card.querySelector('#profile-edit-btn').addEventListener('click', () => {
    const slot = card.querySelector('#profile-editor');
    if (slot.dataset.open === '1') { slot.innerHTML = ''; slot.dataset.open = '0'; return; }
    slot.dataset.open = '1';
    buildEditor(slot, profile, host);
  });

  return card;
}

function buildEditor(slot, profile, host) {
  let chosen = profile.avatar || '';
  slot.innerHTML = `
    <form id="profile-form" autocomplete="off" class="profile-form">
      <div class="ch-form-grid">
        <div class="field"><label>Display name</label>
          <input name="display_name" maxlength="80" value="${escapeHtml(profile.display_name || '')}" placeholder="${escapeHtml(profile.username)}" /></div>
        <div class="field"><label>Email</label>
          <input name="email" type="email" maxlength="255" value="${escapeHtml(profile.email || '')}" placeholder="you@example.com" /></div>
        <div class="field span-2"><label>Bio</label>
          <textarea name="bio" rows="3" maxlength="2000" placeholder="A line or two about you.">${escapeHtml(profile.bio || '')}</textarea></div>
      </div>
      <label style="display:block;margin:.6rem 0 .3rem;font-size:.78rem;color:var(--fg-dim);">Avatar</label>
      <div class="avatar-grid" id="avatar-grid">
        ${AVATARS.map((a) => `<button type="button" class="avatar-opt ${a === chosen ? 'selected' : ''}" data-av="${escapeHtml(a)}">${a}</button>`).join('')}
        <button type="button" class="avatar-opt avatar-clear ${chosen === '' ? 'selected' : ''}" data-av="" title="Use my initial">Aa</button>
      </div>
      <div style="display:flex;gap:.6rem;align-items:center;margin-top:.8rem;">
        <button class="btn" type="submit" id="profile-save">Save changes</button>
        <button class="btn btn-ghost" type="button" id="profile-cancel">Cancel</button>
        <span id="profile-msg" class="msg" style="margin:0;"></span>
      </div>
    </form>
  `;

  const grid = slot.querySelector('#avatar-grid');
  grid.addEventListener('click', (ev) => {
    const b = ev.target.closest('.avatar-opt');
    if (!b) return;
    chosen = b.dataset.av;
    grid.querySelectorAll('.avatar-opt').forEach((n) => n.classList.toggle('selected', n === b));
  });

  slot.querySelector('#profile-cancel').addEventListener('click', () => {
    slot.innerHTML = ''; slot.dataset.open = '0';
  });

  slot.querySelector('#profile-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const form = ev.currentTarget;
    const btn  = slot.querySelector('#profile-save');
    btn.disabled = true; btn.textContent = 'Saving…';
    try {
      const updated = await iam('PUT', '/profile', { auth: true, body: {
        display_name: form.elements.display_name.value,
        email:        form.elements.email.value,
        bio:          form.elements.bio.value,
        avatar:       chosen,
      }});
      // Reflect into shared state + the sidebar, then re-render the page.
      if (state.me) {
        state.me.display_name = updated.display_name;
        state.me.avatar = updated.avatar;
        state.me.email = updated.email;
      }
      paintSidebarIdentity(updated);
      const outlet = $('router-outlet');
      outlet.innerHTML = '';
      await renderProfile(outlet);
    } catch (err) {
      await modalAlert({ title: 'Save failed', message: err.message || 'Could not save your profile.', variant: 'danger' });
      btn.disabled = false; btn.textContent = 'Save changes';
    }
  });
}

// Keep the sidebar identity (avatar glyph + name) in sync after an edit.
export function paintSidebarIdentity(p) {
  const av = $('avatar');
  if (av) av.textContent = avatarGlyph(p);
  const who = $('who-name');
  if (who) who.textContent = p.display_name || p.username || '—';
}

// Role-specific statistics, badges, activity

async function buildStats(profile, me) {
  const wrap = el('div');

  // Headline stats differ by role.
  if (isAdmin()) {
    const [users, sessions, chalStats, lstats] = await Promise.all([
      safe(() => iam('GET', '/users', { auth: true }), []),
      safe(() => scenario('GET', '/sessions', { auth: true }), []),
      safe(() => scenario('GET', '/challenges/stats', { auth: true }), null),
      safe(() => learning('GET', '/stats', { auth: true }), null),
    ]);
    const running = (sessions || []).filter((s) => s.status === 'running').length;
    wrap.appendChild(statRow([
      { label: 'Platform users', value: users.length },
      { label: 'Challenges', value: chalStats ? chalStats.total_challenges : 0 },
      { label: 'Active sessions', value: running },
      { label: 'Total solves', value: lstats ? lstats.total_solves : (chalStats ? chalStats.total_solves : 0) },
    ]));
  } else if (hasAnyRole('instructor')) {
    const [sessions, challenges, board] = await Promise.all([
      safe(() => scenario('GET', '/sessions', { auth: true }), []),
      safe(() => scenario('GET', '/challenges', { auth: true }), []),
      safe(() => learning('GET', '/leaderboard?limit=500', { auth: true }), []),
    ]);
    const running = (sessions || []).filter((s) => s.status === 'running').length;
    wrap.appendChild(statRow([
      { label: 'Active sessions', value: running },
      { label: 'Challenges', value: (challenges || []).length },
      { label: 'Players tracked', value: (board || []).length },
      { label: 'Need attention', value: (board || []).filter((p) => p.solve_count === 0).length },
    ]));
  } else {
    // Player.
    wrap.appendChild(statRow([
      { label: 'Score', value: me ? me.total_score : 0 },
      { label: 'Rank', value: me ? `#${me.rank}` : '—' },
      { label: 'Solved', value: me ? me.solve_count : 0 },
      { label: 'Team', value: me && me.team ? `<span class="badge muted">${escapeHtml(me.team)}</span>` : '<span class="hint">—</span>', small: true },
    ]));
  }

  // Badges — players earn achievements from their solves.
  if (!isAdmin() && !hasAnyRole('instructor')) {
    wrap.appendChild(sectionHead('Badges'));
    wrap.appendChild(badgesPanel(me));
  }

  // Activity timeline — solve history (resolved to challenge names).
  wrap.appendChild(sectionHead('Activity'));
  wrap.appendChild(await timelinePanel(me));

  return wrap;
}

function statRow(items) {
  const stats = el('div', { class: 'stats' });
  stats.innerHTML = items.map((i) => `
    <div class="stat">
      <div class="label">${escapeHtml(i.label)}</div>
      <div class="value ${i.small ? 'small' : ''}">${i.value}</div>
    </div>`).join('');
  return stats;
}

function sectionHead(title) {
  return el('div', { class: 'section-head', style: 'margin-top:1.25rem;' }, el('h2', {}, title));
}

function badgesPanel(me) {
  const solves = (me && me.solves) || [];
  const count = solves.length;
  const byDiff = { easy: 0, medium: 0, hard: 0 };
  solves.forEach((s) => { if (s.difficulty in byDiff) byDiff[s.difficulty] += 1; });

  const badges = [];
  if (count >= 1)  badges.push(['🎯', 'First Solve']);
  if (count >= 5)  badges.push(['🔥', '5 Solves']);
  if (count >= 10) badges.push(['💎', '10 Solves']);
  if (byDiff.medium >= 1) badges.push(['⚙️', 'Medium Slayer']);
  if (byDiff.hard >= 1)   badges.push(['🧠', 'Hard Mode']);
  if (me && me.rank && me.rank <= 3) badges.push(['🏆', 'Top 3']);

  const panel = el('div', { class: 'panel' });
  if (!badges.length) {
    panel.innerHTML = '<div class="empty">Solve your first challenge to start earning badges.</div>';
    return panel;
  }
  panel.innerHTML = `<div class="badge-grid">${badges.map(([icon, label]) => `
    <div class="badge-card">
      <div class="badge-icon">${icon}</div>
      <div class="badge-label">${escapeHtml(label)}</div>
    </div>`).join('')}</div>`;
  return panel;
}

async function timelinePanel(me) {
  const solves = [...((me && me.solves) || [])]
    .sort((a, b) => new Date(b.solved_at) - new Date(a.solved_at));
  const panel = el('div', { class: 'panel' });
  if (!solves.length) {
    panel.innerHTML = '<div class="empty">No activity yet. Go solve something. 🚩</div>';
    return panel;
  }
  // Resolve challenge ids → names.
  const challenges = await safe(() => scenario('GET', '/challenges', { auth: true }), []);
  const titleById = new Map((challenges || []).map((c) => [c.id, c.title]));

  panel.innerHTML = `<ul class="timeline">${solves.slice(0, 20).map((s) => {
    const when = new Date(s.solved_at);
    const whenStr = isNaN(when) ? s.solved_at : when.toLocaleString();
    const name = titleById.get(s.challenge_id) || (s.challenge_id ? s.challenge_id.slice(0, 8) + '…' : '—');
    return `<li class="timeline-item">
      <span class="timeline-dot"></span>
      <div class="timeline-body">
        <div>Solved <strong>${escapeHtml(name)}</strong> <span class="badge role-${escapeHtml(s.difficulty || 'easy')}">${escapeHtml(s.difficulty || '—')}</span> <span class="mono" style="color:var(--ok);">+${s.points}</span></div>
        <div class="hint mono" style="font-size:.72rem;">${escapeHtml(whenStr)}</div>
      </div>
    </li>`;
  }).join('')}</ul>`;
  return panel;
}
