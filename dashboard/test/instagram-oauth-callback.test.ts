import { test } from "node:test";
import assert from "node:assert/strict";
import { NextRequest } from "next/server";
import { makeTestDb } from "./helpers.ts";

let setupPromise: Promise<{
  q: typeof import("../lib/queries.ts");
  GET: typeof import("../app/api/channels/instagram/callback/route.ts").GET;
}> | null = null;
let seq = 0;

async function setup() {
  if (!setupPromise) {
    process.env.META_APP_ID = "test-meta-app";
    process.env.INSTAGRAM_APP_SECRET = "test-instagram-secret";
    process.env.INSTAGRAM_REDIRECT_URI = "https://scheduler.example.test/api/channels/instagram/callback";
    process.env.META_GRAPH_VERSION = "v27.0";
    makeTestDb();
    const [q, route] = await Promise.all([
      import("../lib/queries.ts"),
      import("../app/api/channels/instagram/callback/route.ts"),
    ]);
    setupPromise = Promise.resolve({ q, GET: route.GET });
  }
  return { ...(await setupPromise), suffix: ++seq };
}

function callbackRequest(channelId: number, state = "valid-state") {
  return new NextRequest(
    `https://attacker.invalid/api/channels/instagram/callback?code=auth-code&state=${state}`,
    {
      headers: {
        cookie: `instagram_oauth_state=valid-state; instagram_oauth_channel_id=${channelId}`,
      },
    },
  );
}

function jsonResponse(payload: object) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

test("callback exchanges tokens server-side and stores Instagram Login on the selected channel", async () => {
  const { q, GET, suffix } = await setup();
  const channelId = q.createChannel({
    platform: "instagram",
    account_name: `Callback target ${suffix}`,
    business_label: "Keep label",
    timezone: "Asia/Aden",
    requires_approval: true,
    color_hue: 91,
  });
  const originalFetch = globalThis.fetch;
  const requested: string[] = [];

  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    requested.push(url);
    if (url.startsWith("https://api.instagram.com/oauth/access_token")) {
      return jsonResponse({ data: [{ access_token: "short-lived-secret", user_id: "app-scoped-id", permissions: "instagram_business_basic" }] });
    }
    if (url.startsWith("https://graph.instagram.com/access_token?")) {
      assert.match(url, /grant_type=ig_exchange_token/);
      return jsonResponse({
        access_token: "long-lived-secret",
        token_type: "bearer",
        expires_in: 5184000,
      });
    }
    if (url.startsWith("https://graph.instagram.com/v27.0/me?")) {
      assert.match(url, /fields=user_id%2Cusername/);
      return jsonResponse({ data: [{ user_id: "ig-user-900", username: "test_creator" }] });
    }
    throw new Error(`Unexpected mocked Meta URL: ${url}`);
  }) as typeof fetch;

  try {
    const response = await GET(callbackRequest(channelId));
    const location = response.headers.get("location") ?? "";
    const channel = q.getChannel(channelId)!;

    assert.equal(response.status, 307);
    assert.equal(new URL(location).origin, "https://scheduler.example.test");
    assert.match(location, /instagram_connected=/);
    assert.match(location, /instagram_username=test_creator/);
    assert.equal(location.includes("long-lived-secret"), false);
    assert.deepEqual(requested.length, 3);
    assert.equal(channel.remote_account_id, "ig-user-900");
    assert.equal(channel.access_token, "long-lived-secret");
    assert.equal(channel.instagram_auth_method, "instagram_login");
    assert.equal(channel.account_name, `Callback target ${suffix}`);
    assert.equal(channel.business_label, "Keep label");
    assert.equal(channel.timezone, "Asia/Aden");
    assert.equal(channel.requires_approval, 1);
    assert.equal(channel.color_hue, 91);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("callback rejects a state mismatch without calling Meta or changing the channel", async () => {
  const { q, GET, suffix } = await setup();
  const channelId = q.createChannel({
    platform: "instagram",
    account_name: `State mismatch ${suffix}`,
    timezone: "UTC",
    access_token: "keep-existing-token",
  });
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = (async () => {
    calls += 1;
    throw new Error("Meta must not be called for an invalid state");
  }) as typeof fetch;

  try {
    const request = new NextRequest(
      "https://scheduler.example.test/api/channels/instagram/callback?code=auth-code&state=wrong-state",
      {
        headers: {
          cookie: `instagram_oauth_state=valid-state; instagram_oauth_channel_id=${channelId}`,
        },
      },
    );
    const response = await GET(request);
    const channel = q.getChannel(channelId)!;

    assert.equal(calls, 0);
    assert.match(response.headers.get("location") ?? "", /instagram_error=/);
    assert.equal(channel.access_token, "keep-existing-token");
    assert.equal(channel.instagram_auth_method, "legacy");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("declined Instagram authorization leaves channel credentials untouched", async () => {
  const { q, GET, suffix } = await setup();
  const channelId = q.createChannel({
    platform: "instagram",
    account_name: `Declined ${suffix}`,
    timezone: "UTC",
    remote_account_id: "existing-ig-id",
    access_token: "existing-token",
  });
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = (async () => {
    calls += 1;
    throw new Error("Meta must not be called after a user denial");
  }) as typeof fetch;

  try {
    const request = new NextRequest(
      `https://scheduler.example.test/api/channels/instagram/callback?error=access_denied&state=valid-state`,
      {
        headers: {
          cookie: `instagram_oauth_state=valid-state; instagram_oauth_channel_id=${channelId}`,
        },
      },
    );
    const response = await GET(request);
    const channel = q.getChannel(channelId)!;

    assert.equal(calls, 0);
    assert.match(response.headers.get("location") ?? "", /instagram_error=/);
    assert.equal(channel.remote_account_id, "existing-ig-id");
    assert.equal(channel.access_token, "existing-token");
    assert.equal(channel.instagram_auth_method, "legacy");
  } finally {
    globalThis.fetch = originalFetch;
  }
});


test("callback refuses an ambiguous Meta identity response without storing credentials", async () => {
  const { q, GET, suffix } = await setup();
  const channelId = q.createChannel({
    platform: "instagram",
    account_name: `Ambiguous identity ${suffix}`,
    timezone: "UTC",
    remote_account_id: "old-account-id",
    access_token: "old-token",
  });
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("https://api.instagram.com/oauth/access_token")) {
      return jsonResponse({ access_token: "short-lived-secret", expires_in: 3600 });
    }
    if (url.startsWith("https://graph.instagram.com/access_token?")) {
      return jsonResponse({ access_token: "new-long-lived-secret", expires_in: 5184000 });
    }
    if (url.startsWith("https://graph.instagram.com/v27.0/me?")) {
      return jsonResponse({
        data: [
          { user_id: "ig-user-one", username: "creator_one" },
          { user_id: "ig-user-two", username: "creator_two" },
        ],
      });
    }
    throw new Error(`Unexpected mocked Meta URL: ${url}`);
  }) as typeof fetch;

  try {
    const response = await GET(callbackRequest(channelId));
    const channel = q.getChannel(channelId)!;
    assert.match(response.headers.get("location") ?? "", /instagram_error=/);
    assert.equal(channel.remote_account_id, "old-account-id");
    assert.equal(channel.access_token, "old-token");
    assert.equal(channel.instagram_auth_method, "legacy");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
