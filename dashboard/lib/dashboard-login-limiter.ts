import "server-only";
import { createHash } from "node:crypto";
import { getDb } from "./db";

export const DASHBOARD_LOGIN_MAX_FAILURES = 5;
export const DASHBOARD_LOGIN_WINDOW_MS = 15 * 60 * 1000;

function clientKey(clientIp: string): string {
  // Store only a one-way digest, never a raw client IP address.
  return createHash("sha256").update(clientIp || "unknown", "utf8").digest("hex");
}

function ensureTable(): void {
  getDb().exec(`
    CREATE TABLE IF NOT EXISTS dashboard_login_rate_limits (
      client_key TEXT PRIMARY KEY,
      window_started_at INTEGER NOT NULL,
      failure_count INTEGER NOT NULL,
      blocked_until INTEGER NOT NULL
    )
  `);
}

export function dashboardLoginRetryAfter(clientIp: string, now = Date.now()): number {
  ensureTable();
  const db = getDb();
  db.prepare("DELETE FROM dashboard_login_rate_limits WHERE window_started_at + ? <= ?")
    .run(DASHBOARD_LOGIN_WINDOW_MS, now);
  const entry = db.prepare(
    "SELECT failure_count, blocked_until FROM dashboard_login_rate_limits WHERE client_key = ?",
  ).get(clientKey(clientIp)) as { failure_count: number; blocked_until: number } | undefined;
  return entry && entry.failure_count >= DASHBOARD_LOGIN_MAX_FAILURES && entry.blocked_until > now
    ? Math.ceil((entry.blocked_until - now) / 1000)
    : 0;
}

export function recordDashboardLoginFailure(clientIp: string, now = Date.now()): number {
  ensureTable();
  const db = getDb();
  const key = clientKey(clientIp);
  const record = db.transaction(() => {
    db.prepare("DELETE FROM dashboard_login_rate_limits WHERE window_started_at + ? <= ?")
      .run(DASHBOARD_LOGIN_WINDOW_MS, now);
    const existing = db.prepare(
      "SELECT window_started_at, failure_count, blocked_until FROM dashboard_login_rate_limits WHERE client_key = ?",
    ).get(key) as {
      window_started_at: number;
      failure_count: number;
      blocked_until: number;
    } | undefined;

    if (existing && existing.failure_count >= DASHBOARD_LOGIN_MAX_FAILURES && existing.blocked_until > now) {
      return Math.ceil((existing.blocked_until - now) / 1000);
    }

    const inWindow = Boolean(existing && now - existing.window_started_at < DASHBOARD_LOGIN_WINDOW_MS);
    const windowStartedAt = inWindow ? existing!.window_started_at : now;
    const failureCount = inWindow ? existing!.failure_count + 1 : 1;
    const blockedUntil = windowStartedAt + DASHBOARD_LOGIN_WINDOW_MS;

    db.prepare(`
      INSERT INTO dashboard_login_rate_limits
        (client_key, window_started_at, failure_count, blocked_until)
      VALUES (?, ?, ?, ?)
      ON CONFLICT(client_key) DO UPDATE SET
        window_started_at = excluded.window_started_at,
        failure_count = excluded.failure_count,
        blocked_until = excluded.blocked_until
    `).run(key, windowStartedAt, failureCount, blockedUntil);

    return failureCount >= DASHBOARD_LOGIN_MAX_FAILURES
      ? Math.max(1, Math.ceil((blockedUntil - now) / 1000))
      : 0;
  });
  return record();
}

export function resetDashboardLoginFailures(clientIp: string): void {
  ensureTable();
  getDb().prepare("DELETE FROM dashboard_login_rate_limits WHERE client_key = ?")
    .run(clientKey(clientIp));
}
