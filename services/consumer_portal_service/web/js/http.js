import { state } from './state.js';

// The Consumer & Portal service is the single edge; every base below is a
// gateway prefix the portal proxies to the owning service.
const IAM_BASE        = '/iam';         // → iam-service
const SCENARIO_BASE   = '/scenario';    // → scenario-service
const EVENTS_BASE     = '/events';      // → events-service (announcements + WS)
const MONITORING_BASE = '/monitoring';  // → monitoring-service
const DATA_BASE       = '/data';        // → data-logging-service (logs/query/export)
const LEARNING_BASE   = '/learning';    // → learning-service (scores/leaderboard)
const ORCH_BASE       = '/orchestration'; // → orchestration-service (launch/teardown)
const LOCAL_BASE      = '';             // served by the portal itself (/api/services)

export async function http(method, url, { body, auth } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (auth && state.token)  headers['Authorization'] = 'Bearer ' + state.token;

  const res = await fetch(url, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  const text = await res.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); }
    catch { data = { detail: text }; }
  }
  if (!res.ok) {
    let msg = (data && (data.detail || data.message)) || `HTTP ${res.status}`;
    // Defensive: flatten structured validation errors to readable text.
    if (Array.isArray(msg)) msg = msg.map((e) => e.msg || JSON.stringify(e)).join('; ');
    const err = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    err.status = res.status;
    throw err;
  }
  return data;
}

export const iam        = (method, path, opts) => http(method, IAM_BASE        + path, opts);
export const scenario   = (method, path, opts) => http(method, SCENARIO_BASE   + path, opts);
export const events     = (method, path, opts) => http(method, EVENTS_BASE     + path, opts);
export const monitoring = (method, path, opts) => http(method, MONITORING_BASE + path, opts);
export const data       = (method, path, opts) => http(method, DATA_BASE       + path, opts);
export const learning   = (method, path, opts) => http(method, LEARNING_BASE   + path, opts);
export const orchestration = (method, path, opts) => http(method, ORCH_BASE     + path, opts);
export const local      = (method, path, opts) => http(method, LOCAL_BASE      + path, opts);

// Base path for the Data & Logging service — used for token-authed downloads
// (export) where we need the raw URL rather than the JSON helper.
export const DATA_PREFIX = DATA_BASE;

// Multipart form-data upload — used by the challenges instructor view.
// The browser sets Content-Type with the right boundary; we must not.
export async function uploadForm(method, path, formData) {
  const headers = { Accept: 'application/json' };
  if (state.token) headers['Authorization'] = 'Bearer ' + state.token;

  const res  = await fetch(SCENARIO_BASE + path, { method, headers, body: formData });
  const text = await res.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); }
    catch { data = { detail: text }; }
  }
  if (!res.ok) {
    let msg = (data && (data.detail || data.message)) || `HTTP ${res.status}`;
    // Defensive: flatten structured validation errors to readable text.
    if (Array.isArray(msg)) msg = msg.map((e) => e.msg || JSON.stringify(e)).join('; ');
    const err = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
    err.status = res.status;
    throw err;
  }
  return data;
}
