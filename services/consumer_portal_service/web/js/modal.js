import { el, escapeHtml } from './dom.js';

const ICONS = {
  info:    '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
  success: '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><path d="M22 4 12 14.01l-3-3"/>',
  warning: '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  danger:  '<circle cx="12" cy="12" r="10"/><path d="M15 9l-6 6"/><path d="M9 9l6 6"/>',
};

const VARIANTS = new Set(Object.keys(ICONS));
const variantOf = (v) => (VARIANTS.has(v) ? v : 'info');

// Low-level primitive
//
// actions: [{ label, value, variant?, ghost?, autofocus? }]. Clicking an action
// resolves the modal with its `value`. `body` may be a string (escaped text) or
// a DOM Node (rendered as-is). Returns { close(value) } for programmatic close.
export function openModal({ title = '', message = '', body = null, variant = 'info', actions = [], dismissable = true } = {}) {
  const v = variantOf(variant);
  const lastFocus = document.activeElement;

  const overlay = el('div', { class: 'modal-overlay' });
  const card    = el('div', { class: `modal panel modal-dialog modal-${v}`, role: 'dialog', 'aria-modal': 'true' });

  const head = el('div', { class: 'modal-head' });
  head.innerHTML = `
    <span class="modal-icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[v]}</svg></span>
    <h3 class="modal-title">${escapeHtml(title)}</h3>
  `;
  card.appendChild(head);

  const bodyWrap = el('div', { class: 'modal-body' });
  if (body instanceof Node) bodyWrap.appendChild(body);
  else if (message)         bodyWrap.appendChild(el('p', { class: 'modal-message' }, message));
  card.appendChild(bodyWrap);

  const actionRow = el('div', { class: 'modal-actions' });
  card.appendChild(actionRow);
  overlay.appendChild(card);

  let settled = false;
  function close(value) {
    if (settled) return;
    settled = true;
    overlay.classList.add('closing');
    document.removeEventListener('keydown', onKey, true);
    overlay.remove();
    if (lastFocus && typeof lastFocus.focus === 'function') {
      try { lastFocus.focus(); } catch { /* node gone */ }
    }
    resolveOuter(value);
  }

  let resolveOuter;
  const done = new Promise((res) => { resolveOuter = res; });

  let autoBtn = null;
  actions.forEach((a) => {
    const cls = ['btn'];
    if (a.ghost)   cls.push('btn-ghost');
    if (a.variant === 'danger') cls.push('btn-danger');
    const btn = el('button', { type: 'button', class: cls.join(' ') }, a.label);
    btn.addEventListener('click', () => close(a.value));
    if (a.autofocus) autoBtn = btn;
    actionRow.appendChild(btn);
  });

  function onKey(ev) {
    if (ev.key === 'Escape' && dismissable) { ev.preventDefault(); close(undefined); }
  }
  document.addEventListener('keydown', onKey, true);

  if (dismissable) {
    overlay.addEventListener('mousedown', (ev) => { if (ev.target === overlay) close(undefined); });
  }

  document.body.appendChild(overlay);
  // Focus the requested action (or the first one) so Enter / Esc work at once.
  (autoBtn || actionRow.querySelector('.btn'))?.focus();

  done.close = close;
  // Return both the promise (awaitable) and a close handle.
  return Object.assign(done, { close });
}

// alert
export function modalAlert({ title = 'Notice', message = '', variant = 'info', okText = 'OK' } = {}) {
  return openModal({
    title, message, variant,
    actions: [{ label: okText, value: true, variant: variant === 'danger' ? 'danger' : undefined, autofocus: true }],
  });
}

// confirm
export function modalConfirm({
  title = 'Are you sure?', message = '', variant = 'warning',
  confirmText = 'Confirm', cancelText = 'Cancel',
} = {}) {
  return openModal({
    title, message, variant,
    actions: [
      { label: cancelText,  value: false, ghost: true },
      { label: confirmText, value: true,  variant: variant === 'danger' ? 'danger' : undefined, autofocus: true },
    ],
  }).then((v) => v === true);
}

// prompt
// Resolves the entered string, or null if cancelled / dismissed.
export function modalPrompt({
  title = '', message = '', label = '', value = '', placeholder = '',
  variant = 'info', confirmText = 'OK', cancelText = 'Cancel',
} = {}) {
  const field = el('div', { class: 'modal-field' });
  if (label) field.appendChild(el('label', {}, label));
  const input = el('input', { type: 'text', value: value ?? '', placeholder });
  field.appendChild(input);

  const body = el('div');
  if (message) body.appendChild(el('p', { class: 'modal-message' }, message));
  body.appendChild(field);

  const m = openModal({
    title, body, variant,
    actions: [
      { label: cancelText,  value: null,      ghost: true },
      { label: confirmText, value: '\0__OK__', variant: variant === 'danger' ? 'danger' : undefined },
    ],
  });

  // Map the OK sentinel to the input's value; Enter submits, Esc cancels.
  input.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') { ev.preventDefault(); m.close('\0__OK__'); }
  });
  setTimeout(() => input.focus(), 0);

  return m.then((v) => (v === '\0__OK__' ? input.value : null));
}
