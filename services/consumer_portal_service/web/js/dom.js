export const $ = (id) => document.getElementById(id);

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class')                      node.className = v;
    else if (k === 'html')                  node.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function')
                                            node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null)      node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    node.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
  '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;',
}[c]));

export function setMsg(id, text, kind) {
  const node = $(id);
  node.textContent = text;
  node.classList.remove('show', 'error', 'success');
  if (text) {
    node.classList.add('show');
    if (kind) node.classList.add(kind);
  }
}

export function setBusy(btnId, busy, label) {
  const b = $(btnId);
  b.disabled = busy;
  if (busy) {
    b.dataset.prev = b.innerHTML;
    b.innerHTML = '<span class="spinner"></span>' + (label ? ' ' + label : '');
  } else if (b.dataset.prev) {
    b.innerHTML = b.dataset.prev;
    delete b.dataset.prev;
  }
}
