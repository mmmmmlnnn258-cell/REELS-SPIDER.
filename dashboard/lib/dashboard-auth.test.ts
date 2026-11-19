import { test } from "node:test";
import assert from "node:assert/strict";
import {
  createDashboardSession,
  dashboardAuthConfigured,
  verifyDashboardCredentials,
  verifyDashboardSession,
} from "./dashboard-auth";

const original = {
  username: process.env.DASHBOARD_USERNAME,
  password: process.env.DASHBOARD_PASSWORD,
  secret: process.env.DASHBOARD_SESSION_SECRET,
};

function restoreEnv() {
  for (const [key, value] of [
    ["DASHBOARD_USERNAME", original.username],
    ["DASHBOARD_PASSWORD", original.password],
    ["DASHBOARD_SESSION_SECRET", original.secret],
  ] as const) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
}

test("dashboard auth fails closed until all credentials and a long session secret exist", () => {
  delete process.env.DASHBOARD_USERNAME;
  delete process.env.DASHBOARD_PASSWORD;
  delete process.env.DASHBOARD_SESSION_SECRET;
  assert.equal(dashboardAuthConfigured(), false);
  assert.equal(createDashboardSession(), null);
  assert.equal(verifyDashboardCredentials("owner", "password"), false);
  restoreEnv();
});

test("dashboard credentials are checked and signed sessions expire", () => {
  process.env.DASHBOARD_USERNAME = "owner";
  process.env.DASHBOARD_PASSWORD = "a-long-test-password";
  process.env.DASHBOARD_SESSION_SECRET = "test-session-secret-with-at-least-32-characters";
  try {
    assert.equal(dashboardAuthConfigured(), true);
    assert.equal(verifyDashboardCredentials("owner", "a-long-test-password"), true);
    assert.equal(verifyDashboardCredentials("owner", "wrong-password"), false);
    const now = 1_800_000_000_000;
    const session = createDashboardSession(now)!;
    assert.equal(verifyDashboardSession(session.token, now), true);
    assert.equal(verifyDashboardSession(session.token, now + 13 * 60 * 60 * 1000), false);
    assert.equal(verifyDashboardSession(session.token + "x", now), false);
  } finally {
    restoreEnv();
  }
});
