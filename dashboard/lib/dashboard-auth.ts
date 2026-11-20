import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";

export const DASHBOARD_SESSION_COOKIE = "ss_dashboard_session";
export const DASHBOARD_SESSION_TTL_SECONDS = 12 * 60 * 60;
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
