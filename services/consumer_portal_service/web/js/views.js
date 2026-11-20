import { state, isAdmin } from './state.js';
import { iam }            from './http.js';
import { el, escapeHtml } from './dom.js';
import { modalConfirm, modalAlert } from './modal.js';
import { route }          from './router.js';

// Users (admin only)

export async function renderUsers(outlet) {
  outlet.appendChild(el('div', { class: 'page-head' },
    el('h1', {}, 'Users & Roles'),
    el('p',  {}, 'Manage who has access to the range.'),
  ));

  const wrap = el('div', { class: 'table-wrap' });
  wrap.innerHTML = `
    <table>
      <thead><tr>
        <th class="col-id">ID</th>
        <th>Username</th>
        <th>Roles</th>
        <th style="text-align:right;">Actions</th>
      </tr></thead>
      <tbody id="users-body">
        <tr><td colspan="4" class="empty"><span class="spinner"></span> loading…</td></tr>
      </tbody>
    </table>
  `;
  outlet.appendChild(wrap);

  const body = wrap.querySelector('#users-body');
  let users;
  try {
    users = await iam('GET', '/users', { auth: true });
  } catch (err) {
    body.innerHTML = err.status === 403
      ? `<tr><td colspan="4" class="empty">403 — admin role required to view users.</td></tr>`
      : `<tr><td colspan="4" class="empty">Failed to load: ${escapeHtml(err.message)}</td></tr>`;
    return;
  }

  if (!users.length) {
    body.innerHTML = `<tr><td colspan="4" class="empty">No users yet.</td></tr>`;
    return;
  }

  const admin = isAdmin();
  const ASSIGNABLE_ROLES = ['player', 'instructor', 'admin'];

  body.innerHTML = '';
  users.forEach((u) => {
    const roles = (u.roles || []).map(r =>
      `<span class="badge role-${escapeHtml(r)}">${escapeHtml(r)}</span>`).join('');
    const isSelf = u.id === state.me.id;
    const currentPrimary = (u.roles && u.roles[0]) || 'player';

    const roleSelect = admin
      ? `<select class="role-select" data-user="${escapeHtml(u.id)}" data-username="${escapeHtml(u.username)}">
           ${ASSIGNABLE_ROLES.map(r =>
             `<option value="${r}" ${r === currentPrimary ? 'selected' : ''}>${r}</option>`).join('')}
         </select>`
      : '';

    const delBtn = (admin && !isSelf)
      ? `<button class="btn btn-danger" data-delete="${escapeHtml(u.id)}" data-username="${escapeHtml(u.username)}">Delete</button>`
      : '';

    const tr = el('tr');
    tr.innerHTML = `
      <td class="col-id mono">${escapeHtml(u.id)}</td>
      <td><a href="#/profile?u=${escapeHtml(u.id)}" title="View profile"><strong>${escapeHtml(u.username)}</strong></a>${isSelf ? ' <span class="badge muted">you</span>' : ''}</td>
      <td><div class="roles">${roles || '—'}</div></td>
      <td>
        <div class="actions" style="gap:.4rem;flex-wrap:wrap;justify-content:flex-end;">
          ${roleSelect}
          ${delBtn}
        </div>
      </td>
    `;
    body.appendChild(tr);
  });

  // Role promotion: when the admin picks a new value, PUT the role list.
  body.querySelectorAll('.role-select').forEach((sel) => {
    sel.dataset.previous = sel.value;
    sel.addEventListener('change', async () => {
      const newRole = sel.value;
      const userId  = sel.dataset.user;
      sel.disabled  = true;
      try {
        await iam('PUT', '/users/' + encodeURIComponent(userId) + '/roles', {
          auth: true,
          body: { roles: [newRole] },
        });
        sel.dataset.previous = newRole;
        await route();   // re-render so the role badge column refreshes
      } catch (err) {
        sel.value = sel.dataset.previous;
        await modalAlert({ title: 'Role update failed', message: err.message || 'Could not update role.', variant: 'danger' });
      } finally {
        sel.disabled = false;
      }
    });
  });

  body.querySelectorAll('[data-delete]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const ok = await modalConfirm({
        title: 'Delete user',
        message: `Delete user "${btn.dataset.username}"? This cannot be undone.`,
        variant: 'danger', confirmText: 'Delete',
      });
      if (!ok) return;
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span>';
      try {
        await iam('DELETE', '/users/' + encodeURIComponent(btn.dataset.delete), { auth: true });
        await route();
      } catch (err) {
        await modalAlert({ title: 'Delete failed', message: err.message || 'Could not delete user.', variant: 'danger' });
        btn.disabled = false;
        btn.textContent = 'Delete';
      }
    });
  });
}

// Settings

export function renderSettings(outlet) {
  outlet.appendChild(el('div', { class: 'page-head' },
    el('h1', {}, 'Settings'),
    el('p',  {}, 'Your session details and platform configuration.'),
  ));

  const grid = el('div', { class: 'settings-grid' });
  outlet.appendChild(grid);

  const profile = el('div', { class: 'panel' });
  profile.innerHTML = `
    <h3>Profile</h3>
    <dl class="kv">
      <dt>Username</dt><dd>${escapeHtml(state.me.username || '—')}</dd>
      <dt>User ID</dt><dd>${escapeHtml(state.me.id || '—')}</dd>
      <dt>Email</dt><dd>${escapeHtml(state.me.email || '—')}</dd>
      <dt>Roles</dt><dd>${(state.me.roles || []).join(', ') || '—'}</dd>
    </dl>
  `;
  grid.appendChild(profile);

  const platform = el('div', { class: 'panel' });
  platform.innerHTML = `
    <h3>Platform</h3>
    <dl class="kv">
      <dt>Frontend</dt><dd>${escapeHtml(location.origin)}</dd>
      <dt>IAM</dt><dd>same-origin via /iam</dd>
      <dt>Token</dt><dd>in-memory only · cleared on reload</dd>
    </dl>
  `;
  grid.appendChild(platform);

  // Registration secret — admins can view and rotate the signup secret.
  // Changing it only affects new registrations; existing accounts keep working.
  if (isAdmin()) {
    const reg = el('div', { class: 'panel', style: 'margin-top:1rem;' });
    reg.innerHTML = `
      <h3>Registration secret</h3>
      <p class="hint">New users must supply this secret to create an account.
        Rotating it does not affect existing users.</p>
      <form id="regsec-form" autocomplete="off">
        <div class="ch-form-grid">
          <div class="field"><label>Current secret</label>
            <input id="regsec-value" type="text" readonly value="loading…" /></div>
          <div class="field"><label>New secret</label>
            <input name="secret" type="text" minlength="4" maxlength="128" placeholder="new secret" required /></div>
        </div>
        <div style="display:flex;gap:.6rem;align-items:center;margin-top:.5rem;">
          <button class="btn" type="submit" id="regsec-btn">Update secret</button>
          <span id="regsec-msg" class="msg" style="margin:0;"></span>
        </div>
      </form>
    `;
    outlet.appendChild(reg);

    iam('GET', '/settings/registration-secret', { auth: true })
      .then((r) => { reg.querySelector('#regsec-value').value = r.secret; })
      .catch(() => { reg.querySelector('#regsec-value').value = 'unavailable'; });

    reg.querySelector('#regsec-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const input = ev.currentTarget.elements.secret;
      const msg   = reg.querySelector('#regsec-msg');
      const btn   = reg.querySelector('#regsec-btn');
      msg.classList.remove('show', 'error', 'success');
      btn.disabled = true;
      try {
        const r = await iam('PUT', '/settings/registration-secret',
          { auth: true, body: { secret: input.value.trim() } });
        reg.querySelector('#regsec-value').value = r.secret;
        input.value = '';
        msg.classList.add('show', 'success');
        msg.textContent = 'Registration secret updated.';
      } catch (err) {
        msg.classList.add('show', 'error');
        msg.textContent = err.message || 'Update failed.';
      } finally {
        btn.disabled = false;
      }
    });
  }

  // Change password — calls /iam/auth/change-password.
  const pw = el('div', { class: 'panel', style: 'margin-top:1rem;' });
  pw.innerHTML = `
    <h3>Change password</h3>
    <form id="pw-form" autocomplete="off">
      <div class="ch-form-grid">
        <div class="field"><label>Current password</label>
          <input name="current_password" type="password" autocomplete="current-password" required /></div>
        <div class="field"><label>New password</label>
          <input name="new_password" type="password" autocomplete="new-password" minlength="6" required /></div>
        <div class="field span-2"><label>Confirm new password</label>
          <input name="confirm_password" type="password" autocomplete="new-password" minlength="6" required /></div>
      </div>
      <div style="display:flex;gap:.6rem;align-items:center;margin-top:.5rem;">
        <button class="btn" type="submit" id="pw-btn">Update password</button>
        <span id="pw-msg" class="msg" style="margin:0;"></span>
      </div>
    </form>
  `;
  outlet.appendChild(pw);

  pw.querySelector('#pw-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const form = ev.currentTarget;
    const msg  = pw.querySelector('#pw-msg');
    const btn  = pw.querySelector('#pw-btn');

    const cur = form.elements.current_password.value;
    const nw  = form.elements.new_password.value;
    const cf  = form.elements.confirm_password.value;

    msg.classList.remove('show', 'error', 'success');
    if (nw !== cf) {
      msg.classList.add('show', 'error');
      msg.textContent = 'New password and confirmation do not match.';
      return;
    }
    if (nw.length < 6) {
      msg.classList.add('show', 'error');
      msg.textContent = 'New password must be at least 6 characters.';
      return;
    }
    if (nw === cur) {
      msg.classList.add('show', 'error');
      msg.textContent = 'New password must differ from the current one.';
      return;
    }

    btn.disabled = true;
    btn.textContent = 'Updating…';
    try {
      await iam('POST', '/auth/change-password', {
        auth: true,
        body: { current_password: cur, new_password: nw },
      });
      msg.classList.add('show', 'success');
      msg.textContent = 'Password updated.';
      form.reset();
    } catch (err) {
      msg.classList.add('show', 'error');
      msg.textContent = err.message || 'Update failed.';
    } finally {
      btn.disabled = false;
      btn.textContent = 'Update password';
    }
  });

}

// Coming Soon placeholder

export function renderComingSoon({ code, title, desc, icon }) {
  const node = document.createElement('div');
  node.className = 'coming-soon';
  node.innerHTML = `
    <div class="cs-icon"><svg viewBox="0 0 24 24">${icon}</svg></div>
    <span class="cs-code">${escapeHtml(code)}</span>
    <h2>${escapeHtml(title)}</h2>
    <p>${escapeHtml(desc)}</p>
    <span class="cs-pill">Coming soon</span>
  `;
  return node;
}

