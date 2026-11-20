import { state } from './state.js';

const SEEN_KEY = 'sor.notifications.seenAt';

function storageKey() { return `${SEEN_KEY}:${state.me?.id || 'anon'}`; }

export function getLastSeenAt() {
  try { return localStorage.getItem(storageKey()) || ''; }
  catch { return ''; }
}

export function markAllSeen() {
  try { localStorage.setItem(storageKey(), new Date().toISOString()); }
  catch { /* private mode */ }
  paintBadge(0);
}

const subscribers = new Set();

export function subscribe(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

function dispatch(envelope) {
  for (const fn of subscribers) {
    try { fn(envelope); } catch { /* keep going */ }
  }
}

// Bell UI helpers

export function paintBadge(unread) {
  const dot = document.getElementById('bell-dot');
  if (!dot) return;
  if (unread > 0) {
    dot.classList.add('show');
    dot.textContent = unread > 9 ? '9+' : String(unread);
  } else {
    dot.classList.remove('show');
    dot.textContent = '';
  }
}

// Walk a list of announcements and figure out how many post-date the last
// time the user opened the announcements tab.
export function countUnread(list) {
  const lastSeen = getLastSeenAt();
  if (!lastSeen) return list.length;
  const lastTs = Date.parse(lastSeen);
  if (Number.isNaN(lastTs)) return list.length;
  return list.reduce((n, a) => {
    const t = Date.parse(a.created_at);
    return Number.isFinite(t) && t > lastTs ? n + 1 : n;
  }, 0);
}

// WebSocket client

let socket    = null;
let retry     = 0;
let stopFlag  = false;

export function startEventsSocket() {
  if (!state.token) return;
  stopFlag = false;
  retry    = 0;
  open();
}

export function stopEventsSocket() {
  stopFlag = true;
  if (socket) {
    try { socket.close(); } catch { /* ignore */ }
  }
  socket = null;
}

function open() {
  if (stopFlag || !state.token) return;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url   = `${proto}://${location.host}/events/ws?token=${encodeURIComponent(state.token)}`;

  try {
    socket = new WebSocket(url);
  } catch {
    scheduleReconnect();
    return;
  }

  socket.addEventListener('open', () => { retry = 0; });
  socket.addEventListener('close', () => { socket = null; scheduleReconnect(); });
  socket.addEventListener('error', () => { try { socket?.close(); } catch {} });
  socket.addEventListener('message', (ev) => {
    let env;
    try { env = JSON.parse(ev.data); }
    catch { return; }

    if (env.type === 'event' && env.routing_key &&
        env.routing_key.startsWith('announcement.')) {
      bumpBadge();
    }
    dispatch(env);
  });
}

function scheduleReconnect() {
  if (stopFlag) return;
  retry += 1;
  const delay = Math.min(15_000, 500 * Math.pow(2, retry));   // capped at 15 s
  setTimeout(open, delay);
}

function bumpBadge() {
  const dot = document.getElementById('bell-dot');
  if (!dot) return;
  const current = parseInt(dot.textContent || '0', 10) || 0;
  paintBadge(current + 1);
}
