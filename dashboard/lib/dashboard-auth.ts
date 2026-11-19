import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export const DASHBOARD_SESSION_COOKIE = "ss_dashboard_session";
export const DASHBOARD_SESSION_TTL_SECONDS = 12 * 60 * 60;
export const DASHBOARD_LOGIN_MAX_FAILURES = 5;
export const DASHBOARD_LOGIN_WINDOW_MS = 15 * 60 * 1000;

// Process-local limiter keyed by normalized username. It deliberately avoids trusting
// client-supplied forwarding headers for identity. Expired entries are pruned and the
// map is bounded so arbitrary usernames cannot grow memory without limit.
const loginFailures = new Map<string, { count: number; blockedUntil: number }>();

function loginKey(username: string): string {
  return username.trim().toLowerCase().slice(0, 256);
}

function pruneLoginFailures(now: number): void {
  for (const [key, entry] of loginFailures) {
    if (entry.blockedUntil <= now) loginFailures.delete(key);
  }
  while (loginFailures.size > 10_000) {
    const oldest = loginFailures.keys().next().value;
    if (oldest === undefined) break;
    loginFailures.delete(oldest);
  }
}

export function dashboardLoginRetryAfter(username: string, now = Date.now()): number {
  pruneLoginFailures(now);
  const entry = loginFailures.get(loginKey(username));
  return entry && entry.blockedUntil > now
    ? Math.ceil((entry.blockedUntil - now) / 1000)
    : 0;
}

export function recordDashboardLoginFailure(username: string, now = Date.now()): number {
  pruneLoginFailures(now);
  const key = loginKey(username);
  if (!key) return 0;
  const existing = loginFailures.get(key);
  if (existing && existing.blockedUntil > now) {
    return Math.ceil((existing.blockedUntil - now) / 1000);
  }
  const count = (existing?.count ?? 0) + 1;
  const blockedUntil = count >= DASHBOARD_LOGIN_MAX_FAILURES
    ? now + DASHBOARD_LOGIN_WINDOW_MS
    : now + DASHBOARD_LOGIN_WINDOW_MS;
  loginFailures.delete(key);
  loginFailures.set(key, { count, blockedUntil });
  return count >= DASHBOARD_LOGIN_MAX_FAILURES
    ? Math.ceil(DASHBOARD_LOGIN_WINDOW_MS / 1000)
    : 0;
}

export function resetDashboardLoginFailures(username: string): void {
  loginFailures.delete(loginKey(username));
}

// Test-only reset keeps rate-limit tests isolated without exposing state to callers.
export function resetDashboardLoginLimiterForTests(): void {
  loginFailures.clear();
}

function secret(): string {
  const value = process.env.DASHBOARD_SESSION_SECRET ?? "";
  return value.length >= 32 ? value : "";
}

function equalText(left: string, right: string): boolean {
  const a = createHash("sha256").update(left, "utf8").digest();
  const b = createHash("sha256").update(right, "utf8").digest();
  return timingSafeEqual(a, b);
}

export function dashboardAuthConfigured(): boolean {
  return Boolean(
    process.env.DASHBOARD_USERNAME?.trim() &&
      process.env.DASHBOARD_PASSWORD &&
      secret(),
  );
}

export function verifyDashboardCredentials(username: string, password: string): boolean {
  const expectedUsername = process.env.DASHBOARD_USERNAME ?? "";
  const expectedPassword = process.env.DASHBOARD_PASSWORD ?? "";
  if (!dashboardAuthConfigured()) return false;
  return equalText(username, expectedUsername) && equalText(password, expectedPassword);
}

function signature(payload: string, key: string): string {
  return createHmac("sha256", key).update(payload, "utf8").digest("base64url");
}

export function createDashboardSession(now = Date.now()): {
  token: string;
  expiresAt: number;
} | null {
  const key = secret();
  if (!key || !dashboardAuthConfigured()) return null;
  const expiresAt = Math.floor(now / 1000) + DASHBOARD_SESSION_TTL_SECONDS;
  const nonce = randomBytes(32).toString("hex");
  const payload = `${expiresAt}.${nonce}`;
  return { token: `${payload}.${signature(payload, key)}`, expiresAt };
}

export function verifyDashboardSession(token: string | undefined, now = Date.now()): boolean {
  const key = secret();
  if (!key || !token) return false;
  const match = /^(\d{10})\.([a-f0-9]{64})\.([A-Za-z0-9_-]{43})$/.exec(token);
  if (!match) return false;
  const expiresAt = Number(match[1]);
  if (!Number.isSafeInteger(expiresAt) || expiresAt <= Math.floor(now / 1000)) {
    return false;
  }
  const payload = `${match[1]}.${match[2]}`;
  const expected = signature(payload, key);
  return equalText(match[3], expected);
}
