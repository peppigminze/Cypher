/**
 * Access token handling (M2).
 *
 * The token is no longer baked into the JS bundle (it used to come from
 * VITE_USER_TOKEN, so anyone who could load the PWA had it). Instead:
 *   - on the PC itself, the UI asks the backend for it
 *     (GET /api/auth/session - only answered for same-site requests from
 *     this machine);
 *   - other devices pair once: open the pairing link (#pair=<token>) shown
 *     in the PC's UI, or type the code into the pairing screen.
 * The token is then kept in localStorage on that device.
 */

const TOKEN_KEY = "cypher.token.v1";

export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL || "";

export function getStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function storeToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token.trim());
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

/** Store a token passed as `#pair=<token>` and remove it from the address bar
 * (so it doesn't end up in the browser history or a screenshot). */
export function consumePairingHash(): boolean {
  const match = window.location.hash.match(/(?:^#|&)pair=([A-Za-z0-9_-]{16,128})/);
  if (!match) return false;
  storeToken(match[1]);
  history.replaceState(null, "", window.location.pathname + window.location.search);
  return true;
}

async function tokenIsValid(token: string): Promise<boolean | "offline"> {
  try {
    const resp = await fetch(`${API_BASE_URL}/api/auth/check`, { headers: { Authorization: `Bearer ${token}` } });
    if (resp.status === 401) return false;
    return resp.ok ? true : "offline";
  } catch {
    return "offline";
  }
}

async function fetchLocalSessionToken(): Promise<string | null> {
  try {
    const resp = await fetch(`${API_BASE_URL}/api/auth/session`, { credentials: "omit" });
    if (!resp.ok) return null;
    const body = (await resp.json()) as { token?: string };
    return body.token ?? null;
  } catch {
    return null;
  }
}

export type AuthState = "ready" | "needs_pairing" | "offline";

/** Make sure a usable token is stored. */
export async function ensureToken(): Promise<AuthState> {
  consumePairingHash();
  const stored = getStoredToken();
  if (stored) {
    const valid = await tokenIsValid(stored);
    if (valid === true) return "ready";
    if (valid === "offline") return "offline";
    clearToken();
  }
  const local = await fetchLocalSessionToken();
  if (local) {
    storeToken(local);
    return "ready";
  }
  return "needs_pairing";
}

/** Try a code typed into the pairing screen. */
export async function pairWithCode(code: string): Promise<boolean> {
  const token = code.trim();
  if (!token) return false;
  if ((await tokenIsValid(token)) === true) {
    storeToken(token);
    return true;
  }
  return false;
}

/** Fired when the backend answers 401 - the app then shows the pairing screen. */
export const AUTH_REQUIRED_EVENT = "cypher:auth-required";
