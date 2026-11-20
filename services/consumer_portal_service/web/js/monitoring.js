import { monitoring as monApi } from './http.js';
import { el, escapeHtml } from './dom.js';

let timer = null;

export async function renderMonitoring(outlet) {
  outlet.appendChild(el('div', { class: 'page-head' },
    el('h1', {}, 'Monitoring'),
    el('p',  {}, 'Live service health from Prometheus, dashboards in Grafana.'),
  ));

  // Toolbar with external links (populated once /info resolves).
  const toolbar = el('div', { class: 'event-toolbar', style: 'margin-bottom:1rem;' });
  toolbar.innerHTML = `
    <span class="hint mono" id="mon-status">connecting…</span>
    <span class="spacer"></span>
    <a class="btn btn-ghost btn-sm" id="mon-prom" target="_blank" rel="noopener" style="display:none;">Prometheus</a>
    <a class="btn btn-sm" id="mon-grafana" target="_blank" rel="noopener" style="display:none;">Open Grafana</a>
  `;
  outlet.appendChild(toolbar);

  const tableHost = el('div', { id: 'mon-table' });
  outlet.appendChild(tableHost);

  // Embedded Grafana dashboard (kiosk mode). Anonymous viewer + embedding
  // are enabled on the Grafana container so this loads without a login.
  const frameWrap = el('div', { id: 'mon-frame', style: 'margin-top:1.25rem;' });
  outlet.appendChild(frameWrap);

  await refresh(tableHost, toolbar, frameWrap);

  // Auto-refresh the table every 10 s; clear on teardown.
  timer = setInterval(() => refresh(tableHost, toolbar, null), 10000);
  outlet.addEventListener('sor:teardown', () => {
    if (timer) { clearInterval(timer); timer = null; }
  }, { once: true });
}

async function refresh(tableHost, toolbar, frameWrap) {
  let summary;
  try {
    summary = await monApi('GET', '/summary', { auth: true });
  } catch (err) {
    tableHost.innerHTML = `<div class="empty">Failed to load monitoring: ${escapeHtml(err.message)}</div>`;
    return;
  }

  // Status + external links.
  const status = toolbar.querySelector('#mon-status');
  const promA  = toolbar.querySelector('#mon-prom');
  const grafA  = toolbar.querySelector('#mon-grafana');
  if (summary.prometheus_reachable) {
    status.textContent = `${summary.services_up}/${summary.services_total} services up`;
  } else {
    status.textContent = 'prometheus unreachable';
  }
  if (summary.prometheus_url) { promA.href = summary.prometheus_url; promA.style.display = ''; }
  if (summary.grafana_url)    { grafA.href = summary.grafana_url;    grafA.style.display = ''; }

  // Health table.
  if (!summary.prometheus_reachable) {
    tableHost.innerHTML = '<div class="empty">Prometheus is not reachable from monitoring-service.</div>';
  } else if (!summary.services.length) {
    tableHost.innerHTML = '<div class="empty">No services discovered yet — give Prometheus a scrape interval to find them.</div>';
  } else {
    const wrap = el('div', { class: 'table-wrap' });
    wrap.innerHTML = `
      <table>
        <thead><tr>
          <th>Service</th>
          <th>Status</th>
          <th>Req/s (5m)</th>
          <th>5xx/s</th>
          <th>p95 latency</th>
          <th>Last scrape</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    `;
    const body = wrap.querySelector('tbody');
    summary.services.forEach((s) => {
      const rate = s.request_rate == null ? '—' : s.request_rate.toFixed(2);
      const err  = s.error_rate   == null ? '—' : s.error_rate.toFixed(2);
      const p95  = s.p95_latency_s == null || Number.isNaN(s.p95_latency_s)
                     ? '—' : `${(s.p95_latency_s * 1000).toFixed(0)} ms`;
      const scrape = s.last_scrape ? new Date(s.last_scrape).toLocaleTimeString() : '—';
      const tr = el('tr');
      tr.innerHTML = `
        <td><strong>${escapeHtml(s.service)}</strong>
            <div class="hint mono" style="font-size:.7rem;">${escapeHtml(s.instance || '')}</div></td>
        <td><span class="badge ${s.up ? 'ok' : 'bad'}">${s.up ? 'up' : 'down'}</span>
            ${s.last_error ? `<div class="hint" style="color:var(--danger);font-size:.7rem;">${escapeHtml(s.last_error)}</div>` : ''}</td>
        <td class="mono">${rate}</td>
        <td class="mono">${err}</td>
        <td class="mono">${p95}</td>
        <td class="mono" style="font-size:.78rem;">${escapeHtml(scrape)}</td>
      `;
      body.appendChild(tr);
    });
    tableHost.innerHTML = '';
    tableHost.appendChild(wrap);
  }

  // Embed the Grafana dashboard (only build it once — first call passes a node).
  if (frameWrap && summary.dashboard_url) {
    frameWrap.innerHTML = `
      <div class="section-head"><h2>Grafana dashboard</h2>
        <span class="hint">embedded · kiosk mode</span></div>
      <iframe class="grafana-frame" src="${escapeHtml(summary.dashboard_url)}"
              title="SOR Grafana dashboard" loading="lazy"></iframe>
    `;
  }
}
