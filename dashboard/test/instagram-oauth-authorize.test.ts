import { test } from "node:test";
import assert from "node:assert/strict";
import { NextRequest } from "next/server";
import { makeTestDb } from "./helpers.ts";

let setupPromise: Promise<{
  q: typeof import("../lib/queries.ts");
  POST: typeof import("../app/api/channels/instagram/authorize/route.ts").POST;
}> | null = null;
let seq = 0;

async function setup() {
  if (!setupPromise) {
    process.env.INSTAGRAM_APP_ID = "authorize-test-app";
    process.env.INSTAGRAM_APP_SECRET = "authorize-test-secret";
    process.env.INSTAGRAM_REDIRECT_URI =
      "https://scheduler.example.test/api/channels/instagram/callback";
    makeTestDb();
    const [q, route] = await Promise.all([
      import("../lib/queries.ts"),
      import("../app/api/channels/instagram/authorize/route.ts"),
    ]);
    setupPromise = Promise.resolve({ q, POST: route.POST });
  }
  return { ...(await setupPromise), suffix: ++seq };
}

function authorizeRequest(channelId: string) {
  const body = new FormData();
  body.set("channel_id", channelId);
  return new NextRequest("https://scheduler.example.test/api/channels/instagram/authorize", {
    method: "POST",
    body,
    headers: { origin: "https://scheduler.example.test" },
  });
}

test("authorize starts OAuth for the selected existing Instagram channel only", async () => {
  const { q, POST, suffix } = await setup();
  const channelId = q.createChannel({
    platform: "instagram",
    account_name: `Authorize target ${suffix}`,
    timezone: "Asia/Aden",
  });

  const response = await POST(authorizeRequest(String(channelId)));
  const location = new URL(response.headers.get("location") ?? "");

  assert.equal(response.status, 307);
  assert.equal(location.origin + location.pathname, "https://www.instagram.com/oauth/authorize");
  assert.equal(location.searchParams.get("client_id"), "authorize-test-app");
  assert.equal(location.searchParams.get("response_type"), "code");
  assert.equal(location.searchParams.get("enable_fb_login"), "false");
  assert.match(location.searchParams.get("state") ?? "", /^[0-9a-f]{64}$/);
  const grantedScopes = new Set((location.searchParams.get("scope") ?? "").split(","));
  assert.deepEqual([...grantedScopes].sort(), [
    "instagram_business_basic",
    "instagram_business_content_publish",
    "instagram_business_manage_comments",
    "instagram_business_manage_messages",
  ].sort());
  assert.equal(grantedScopes.has("instagram_business_manage_insights"), false);
  assert.equal(response.headers.get("cache-control"), "no-store");

  const cookies = response.cookies.getAll();
  const stateCookie = cookies.find((cookie) => cookie.name === "instagram_oauth_state");
  const channelCookie = cookies.find((cookie) => cookie.name === "instagram_oauth_channel_id");
  assert.equal(stateCookie?.value, location.searchParams.get("state"));
  assert.equal(stateCookie?.httpOnly, true);
  assert.equal(stateCookie?.sameSite, "lax");
  assert.equal(stateCookie?.maxAge, 600);
  assert.equal(channelCookie?.value, String(channelId));
  assert.equal(channelCookie?.httpOnly, true);
  assert.equal(channelCookie?.maxAge, 600);
  assert.equal(location.href.includes("authorize-test-secret"), false);
});

test("authorize refuses a missing or non-Instagram channel without starting OAuth", async () => {
  const { q, POST, suffix } = await setup();
  const tiktokId = q.createChannel({
    platform: "tiktok",
    account_name: `Not Instagram ${suffix}`,
    timezone: "UTC",
  });

  for (const channelId of ["", "not-a-number", "99999999", String(tiktokId)]) {
    const response = await POST(authorizeRequest(channelId));
    const location = new URL(response.headers.get("location") ?? "");
    assert.equal(response.status, 307);
    assert.equal(location.origin, "https://scheduler.example.test");
    assert.equal(location.pathname, "/channels");
    assert.ok(location.searchParams.has("instagram_error"));
    assert.equal(response.cookies.getAll().length, 0);
  }
});

test("authorize fails closed when Instagram app credentials are absent", async () => {
  const { q, POST, suffix } = await setup();
  const channelId = q.createChannel({
    platform: "instagram",
    account_name: `Missing credentials ${suffix}`,
    timezone: "UTC",
  });
  const original = process.env.INSTAGRAM_APP_SECRET;
  delete process.env.INSTAGRAM_APP_SECRET;
  try {
    const response = await POST(authorizeRequest(String(channelId)));
    assert.equal(response.status, 307);
    const location = new URL(response.headers.get("location") ?? "");
    assert.equal(location.pathname, "/channels");
    assert.match(location.searchParams.get("instagram_error") ?? "", /INSTAGRAM_APP_ID.*INSTAGRAM_APP_SECRET/);
    assert.equal(response.cookies.getAll().length, 0);
  } finally {
    process.env.INSTAGRAM_APP_SECRET = original;
  }
});
