import { test } from "node:test";
import assert from "node:assert/strict";
import { makeTestDb } from "./helpers.ts";

let limiterPromise: Promise<typeof import("../lib/dashboard-login-limiter.ts")> | null = null;

async function limiter() {
  if (!limiterPromise) {
    process.env.DASHBOARD_SESSION_SECRET = "test-login-limiter-secret-with-at-least-32-characters";
    makeTestDb();
    limiterPromise = import("../lib/dashboard-login-limiter.ts");
  }
  return limiterPromise;
}

test("persistent login limiter blocks one client after five failures without locking out another client", async () => {
  const rate = await limiter();
  const start = 1_800_000_000_000;
  for (let i = 0; i < 4; i++) {
    assert.equal(rate.recordDashboardLoginFailure("203.0.113.10", start + i), 0);
    assert.equal(rate.dashboardLoginRetryAfter("203.0.113.10", start + i), 0);
  }
  assert.equal(rate.recordDashboardLoginFailure("203.0.113.10", start + 4), 15 * 60);
  assert.equal(rate.dashboardLoginRetryAfter("203.0.113.10", start + 5), 15 * 60);
  assert.equal(rate.dashboardLoginRetryAfter("203.0.113.11", start + 5), 0);
  assert.equal(rate.dashboardLoginRetryAfter("203.0.113.10", start + 15 * 60 * 1000 + 5), 0);
});

test("successful login clears the client address counter", async () => {
  const rate = await limiter();
  const start = 1_800_000_100_000;
  for (let i = 0; i < 5; i++) rate.recordDashboardLoginFailure("198.51.100.20", start + i);
  assert.ok(rate.dashboardLoginRetryAfter("198.51.100.20", start + 6) > 0);
  rate.resetDashboardLoginFailures("198.51.100.20");
  assert.equal(rate.dashboardLoginRetryAfter("198.51.100.20", start + 7), 0);
});
