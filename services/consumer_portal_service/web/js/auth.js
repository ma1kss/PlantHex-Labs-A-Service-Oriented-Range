import { state, SOR_TOKEN_KEY } from './state.js';
import { iam, events as eventsApi } from './http.js';
import { $, setMsg, setBusy } from './dom.js';
import { showTopView } from './main.js';
import { applyRoleVisibility, route } from './router.js';
import { paintSidebarIdentity } from './profile.js';
import {
  startEventsSocket, stopEventsSocket,
  paintBadge, countUnread,
} from './notifications.js';

export function wireAuthForms() {
  // Switch between login and register cards.
  document.querySelectorAll('[data-goto]').forEach((node) => {
    node.addEventListener('click', (ev) => {
      ev.preventDefault();
      showTopView(node.dataset.goto);
    });
  });

  $('login-form').addEventListener('submit', onLogin);
  $('register-form').addEventListener('submit', onRegister);
  $('logout-btn').addEventListener('click', logout);

  // Sidebar identity (avatar + name) is a shortcut to the profile page.
  const goProfile = () => { location.hash = '#/profile'; };
  const av = $('avatar');
  if (av) { av.style.cursor = 'pointer'; av.title = 'View profile'; av.addEventListener('click', goProfile); }
  const who = document.querySelector('.sidebar-footer .who');
  if (who) { who.style.cursor = 'pointer'; who.addEventListener('click', goProfile); }

  // Bell → Announcements tab.
  const bell = $('bell-btn');
  if (bell) bell.addEventListener('click', () => { location.hash = '#/announcements'; });

  // Forgot password — placeholder only; no backend flow yet.
  const forgot = $('forgot-link');
  if (forgot) forgot.addEventListener('click', (ev) => {
    ev.preventDefault();
    setMsg('login-msg',
      'Password reset is not wired yet — ask an admin to reset your password from Users & Roles.',
      null);
    $('login-msg').classList.add('show');
  });
}

async function onLogin(ev) {
  ev.preventDefault();
  setMsg('login-msg', '', null);

  const username = $('login-username').value.trim();
  const password = $('login-password').value;
  if (!username || !password) {
    setMsg('login-msg', 'Username and password are required.', 'error');
    return;
  }

  setBusy('login-btn', true, 'Signing in');
  try {
    const res = await iam('POST', '/auth/login', { body: { username, password } });
    state.token = res.access_token;
    try { localStorage.setItem(SOR_TOKEN_KEY, state.token); } catch { /* private mode */ }
    await enterPlatform();
  } catch (err) {
    setMsg('login-msg', err.message || 'Login failed.', 'error');
  } finally {
    setBusy('login-btn', false);
  }
}

async function onRegister(ev) {
  ev.preventDefault();
  setMsg('register-msg', '', null);

  const username = $('reg-username').value.trim();
  const password = $('reg-password').value;
  const secret   = $('reg-secret').value;
  if (!username || !password) {
    setMsg('register-msg', 'Username and password are required.', 'error');
    return;
  }
  if (password.length < 6) {
    setMsg('register-msg', 'Password must be at least 6 characters.', 'error');
    return;
  }
  if (!secret) {
    setMsg('register-msg', 'Registration secret is required — ask your instructor.', 'error');
    return;
  }

  setBusy('register-btn', true, 'Creating');
  try {
    // Role is server-side: every new account starts as "player". An admin
    // can promote later from the Users & Roles panel.
    await iam('POST', '/auth/register', {
      body: { username, password, secret },
    });
    setMsg('register-msg', `Account "${username}" created. You can sign in now.`, 'success');
    $('register-form').reset();
    setTimeout(() => showTopView('login'), 900);
  } catch (err) {
    setMsg('register-msg', err.message || 'Registration failed.', 'error');
  } finally {
    setBusy('register-btn', false);
  }
}

export function logout() {
  stopEventsSocket();
  paintBadge(0);
  state.token = null;
  state.me    = null;
  try { localStorage.removeItem(SOR_TOKEN_KEY); } catch { /* private mode */ }
  if (location.hash) history.replaceState(null, '', location.pathname);
  showTopView('login');
  $('login-form').reset();
}

// Called after a successful login: validate the token, populate the sidebar
// identity, filter nav by role, then route.
export async function enterPlatform() {
  try {
    state.me = await iam('GET', '/auth/validate', { auth: true });
    $('token-badge').className = 'badge ok';
    $('token-badge').textContent = 'token: valid';
  } catch {
    $('token-badge').className = 'badge bad';
    $('token-badge').textContent = 'token: invalid';
    logout();
    setMsg('login-msg', 'Session expired. Please sign in again.', 'error');
    return;
  }

  // Enrich the validated identity with the editable profile (display name +
  // preset avatar) so the sidebar shows them. Non-fatal: falls back to the
  // username/initial if the profile call fails.
  try {
    const profile = await iam('GET', '/profile', { auth: true });
    state.me.display_name = profile.display_name || '';
    state.me.avatar       = profile.avatar || '';
    state.me.bio          = profile.bio || '';
    if (profile.email) state.me.email = profile.email;
  } catch { /* keep validate-only identity */ }

  paintSidebarIdentity(state.me);
  $('who-roles').textContent = (state.me.roles || []).join(', ') || 'no roles';

  applyRoleVisibility();

  showTopView('platform');

  // Start the live event-bus subscription + prime the bell badge.
  startEventsSocket();
  primeUnreadBadge();

  if (!location.hash) location.hash = '#/dashboard';
  else                route();
}


async function primeUnreadBadge() {
  try {
    const list = await eventsApi('GET', '/announcements', { auth: true });
    paintBadge(countUnread(list || []));
  } catch {
    paintBadge(0);
  }
}
