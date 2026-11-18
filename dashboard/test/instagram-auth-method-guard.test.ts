import { test } from "node:test";
import assert from "node:assert/strict";
import { NextRequest } from "next/server";
import { makeTestDb } from "./helpers.ts";

let setupPromise: Promise<{
  q: typeof import("../lib/queries.ts");
  PATCH: typeof import("../app/api/channels/[id]/route.ts").PATCH;
  POST: typeof import("../app/api/channels/route.ts").POST;
}> | null = null;

async function setup() {
  if (!setupPromise) {
    makeTestDb();
    const [q, patchRoute, channelsRoute] = await Promise.all([
      import("../lib/queries.ts"),
      import("../app/api/channels/[id]/route.ts"),
      import("../app/api/channels/route.ts"),
    ]);
    setupPromise = Promise.resolve({
      q,
      PATCH: patchRoute.PATCH,
      POST: channelsRoute.POST,
    });
  }
  return setupPromise;
}

test("generic channel routes cannot select a non-legacy Instagram auth method", async () => {
  const { q, PATCH, POST } = await setup();
  const channelId = q.createChannel({
    platform: "instagram",
    account_name: "Protected Instagram channel",
    timezone: "UTC",
    remote_account_id: "existing-instagram-id",
    access_token: "existing-facebook-or-legacy-token",
  });

  const patchResponse = await PATCH(
    new NextRequest(`https://scheduler.example.test/api/channels/${channelId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        instagram_auth_method: "instagram_login",
        remote_account_id: "new-instagram-id",
      }),
    }),
    { params: Promise.resolve({ id: String(channelId) }) },
  );

  assert.equal(patchResponse.status, 400);
  assert.match((await patchResponse.json()).error, /only be changed.*Instagram OAuth/i);
  const unchanged = q.getChannel(channelId)!;
  assert.equal(unchanged.instagram_auth_method, "legacy");
  assert.equal(unchanged.remote_account_id, "existing-instagram-id");
  assert.equal(unchanged.access_token, "existing-facebook-or-legacy-token");

  const createResponse = await POST(
    new NextRequest("https://scheduler.example.test/api/channels", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        platform: "instagram",
        account_name: "Unverified Instagram channel",
        timezone: "UTC",
        remote_account_id: "unverified-id",
        access_token: "unverified-token",
        instagram_auth_method: "instagram_login",
      }),
    }),
  );

  assert.equal(createResponse.status, 400);
  assert.match((await createResponse.json()).error, /only be selected.*Instagram OAuth/i);
  assert.equal(
    q.getChannels().some((channel) => channel.account_name === "Unverified Instagram channel"),
    false,
  );
});
