export const SOR_TOKEN_KEY = 'sor.iam.token';

export const state = {
  token: (() => {
    try { return localStorage.getItem(SOR_TOKEN_KEY); }
    catch { return null; }
  })(),
  me: null,   // UserContext returned by /auth/validate
};

export function isAdmin()      { return (state.me?.roles || []).includes('admin'); }
export function isInstructor() { return (state.me?.roles || []).includes('instructor'); }
export function isTrainee()    { return (state.me?.roles || []).includes('trainee'); }

export function hasAnyRole(...roles) {
  const mine = state.me?.roles || [];
  return roles.some(r => mine.includes(r));
}
