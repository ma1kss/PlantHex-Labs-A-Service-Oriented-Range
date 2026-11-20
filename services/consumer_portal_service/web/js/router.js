import { state, hasAnyRole } from './state.js';
import { $ } from './dom.js';
import {
  renderUsers,
  renderSettings,
  renderComingSoon,
} from './views.js';
import { renderDashboard }   from './dashboard.js';
import { renderChallenges }    from './challenges.js';
import { renderCtfEvents }     from './ctf_events.js';
import { renderAnnouncements } from './announcements.js';
import { renderEventLog }      from './event_log.js';
import { renderMonitoring }    from './monitoring.js';
import { renderScoreboard }    from './learning.js';
import { renderTeams }         from './teams.js';
import { renderProfile }      from './profile.js';

// roles=null means "any signed-in user".
const ROUTES = {
  dashboard:    { title: 'Dashboard',     roles: null,                       render: renderDashboard },
  challenges:   { title: 'Challenges',    roles: null,                       render: renderChallenges },
  events:       { title: 'Events',        roles: null,                       render: renderCtfEvents },
  scoreboard:   { title: 'Scoreboard',    roles: null,                       render: renderScoreboard },
  teams:        { title: 'Teams',         roles: null,                       render: renderTeams },
  announcements:{ title: 'Announcements', roles: null,                       render: renderAnnouncements },
  resources:  { title: 'Resources',     roles: ['instructor', 'admin'],    render: () => renderComingSoon({
    code: 'SOR-SVC-01', title: 'Resources',
    desc: 'Provision and manage the virtual range — VMs, containers, networks, OT/ICS emulators. Service not enabled in this deployment.',
    icon: '<rect x="3" y="4" width="18" height="6" rx="1"/><rect x="3" y="14" width="18" height="6" rx="1"/><path d="M7 7h.01M7 17h.01"/>',
  })},
  monitoring: { title: 'Monitoring',    roles: ['instructor', 'admin'],    render: renderMonitoring },
  'event-log':{ title: 'Event Log',     roles: ['admin'],                   render: renderEventLog },
  users:      { title: 'Users & Roles', roles: ['admin'],                  render: renderUsers },
  profile:    { title: 'Profile',       roles: null,                       render: renderProfile },
  settings:   { title: 'Settings',      roles: null,                       render: renderSettings },
};

function isAllowed(routeDef) {
  return routeDef.roles == null || hasAnyRole(...routeDef.roles);
}

// Show / hide sidebar items based on the current user's roles.
export function applyRoleVisibility() {
  document.querySelectorAll('.nav-item[data-route]').forEach((node) => {
    const def = ROUTES[node.dataset.route];
    node.style.display = (def && isAllowed(def)) ? '' : 'none';
  });

  // Hide a whole nav group if every item in it is hidden.
  document.querySelectorAll('.nav-group').forEach((group) => {
    const items = group.querySelectorAll('.nav-item[data-route]');
    if (!items.length) return;
    const anyVisible = [...items].some(n => n.style.display !== 'none');
    group.classList.toggle('hidden', !anyVisible);
  });
}

export async function route() {
  if (!state.token) return;

  const name = (location.hash.replace(/^#\/?/, '') || 'dashboard').split('?')[0];
  let def = ROUTES[name] || ROUTES.dashboard;

  if (!isAllowed(def)) {
    location.hash = '#/dashboard';
    def = ROUTES.dashboard;
  }

  document.querySelectorAll('.nav-item').forEach(n =>
    n.classList.toggle('active', n.dataset.route === name));
  $('crumb-page').textContent = def.title;

  const outlet = $('router-outlet');
  // Give the previous view a chance to drop its WS subscribers etc.
  outlet.dispatchEvent(new CustomEvent('sor:teardown'));
  outlet.innerHTML = '';
  try {
    // Render functions may either mutate the outlet directly or return a node.
    const result = await def.render(outlet);
    if (result instanceof Node) outlet.appendChild(result);
  } catch (err) {
    outlet.innerHTML = `<div class="empty">Failed to render: ${err.message}</div>`;
  }

  // Close the mobile sidebar after navigating.
  $('sidebar').classList.remove('open');
  $('backdrop').classList.remove('show');
}

export function wireRouter() {
  window.addEventListener('hashchange', route);
  document.querySelectorAll('.nav-item[data-route]').forEach((node) => {
    node.addEventListener('click', () => { location.hash = '#/' + node.dataset.route; });
  });

  $('hamburger').addEventListener('click', () => {
    $('sidebar').classList.toggle('open');
    $('backdrop').classList.toggle('show');
  });
  $('backdrop').addEventListener('click', () => {
    $('sidebar').classList.remove('open');
    $('backdrop').classList.remove('show');
  });
}
