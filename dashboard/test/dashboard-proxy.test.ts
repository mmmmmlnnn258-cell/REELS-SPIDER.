import { test } from "node:test";
import assert from "node:assert/strict";
import { NextRequest } from "next/server";
import { proxy } from "../proxy";
import { createDashboardSession, DASHBOARD_SESSION_COOKIE } from "../lib/dashboard-auth";

const original = {
  username: process.env.DASHBOARD_USERNAME,
  password: process.env.DASHBOARD_PASSWORD,
  secret: process.env.DASHBOARD_SESSION_SECRET,
};

function configure() {
  process.env.DASHBOARD_USERNAME = "owner";
  process.env.DASHBOARD_PASSWORD = "test-password";
  process.env.DASHBOARD_SESSION_SECRET = "proxy-test-secret-with-at-least-32-characters";
}

function restore() {
  for (const [key, value] of [
    ["DASHBOARD_USERNAME", original.username],
    ["DASHBOARD_PASSWORD", original.password],
    ["DASHBOARD_SESSION_SECRET", original.secret],
  ] as const) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
}

test("proxy redirects unauthenticated dashboard pages to login", () => {
  configure();
  try {
    const response = proxy(new NextRequest("https://scheduler.example.test/channels?tab=instagram"));
    assert.equal(response.status, 307);
    assert.equal(
      response.headers.get("location"),
      "https://scheduler.example.test/login?next=%2Fchannels%3Ftab%3Dinstagram",
    );
  } finally {
    restore();
  }
});

test("proxy returns 401 for unauthenticated API reads", () => {
  configure();
  try {
    const response = proxy(new NextRequest("https://scheduler.example.test/api/channels"));
    assert.equal(response.status, 401);
  } finally {
    restore();
  }
});

test("proxy allows OAuth callbacks through for their own state and PKCE checks", () => {
  configure();
  try {
    const instagram = proxy(
      new NextRequest("https://scheduler.example.test/api/channels/instagram/callback?code=x&state=y"),
    );
    const tiktok = proxy(
      new NextRequest("https://scheduler.example.test/api/channels/tiktok/callback?code=x&state=y"),
    );
    assert.equal(instagram.headers.get("x-middleware-next"), "1");
    assert.equal(tiktok.headers.get("x-middleware-next"), "1");
  } finally {
    restore();
  }
});

test("proxy accepts a valid signed session and rejects cross-origin API mutations", () => {
  configure();
  try {
    const session = createDashboardSession()!;
    const page = proxy(
      new NextRequest("https://scheduler.example.test/channels", {
        headers: { cookie: `${DASHBOARD_SESSION_COOKIE}=${session.token}` },
      }),
    );
    assert.equal(page.headers.get("x-middleware-next"), "1");

    const mutation = proxy(
      new NextRequest("https://scheduler.example.test/api/channels", {
        method: "POST",
        headers: {
          cookie: `${DASHBOARD_SESSION_COOKIE}=${session.token}`,
          origin: "https://attacker.example.test",
        },
      }),
    );
    assert.equal(mutation.status, 403);
  } finally {
    restore();
  }
});
