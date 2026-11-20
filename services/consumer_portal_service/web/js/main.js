import { state } from './state.js';
import { setMsg } from './dom.js';
import { wireAuthForms, enterPlatform } from './auth.js';
import { wireRouter }    from './router.js';

const TOP_VIEWS = ['login', 'register', 'platform'];

export function showTopView(name) {
  for (const v of TOP_VIEWS) {
    document.getElementById('view-' + v).classList.toggle('hidden', v !== name);
  }
  setMsg('login-msg', '', null);
  setMsg('register-msg', '', null);
}

wireAuthForms();
wireRouter();

if (state.token) {
  // Try the stashed token — enterPlatform calls /auth/validate and falls
  // back to the login view (via auth.logout) if the server rejects it.
  enterPlatform();
} else {
  showTopView('login');
}
