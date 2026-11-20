import { test } from "node:test";
import assert from "node:assert/strict";
import { makeTestDb } from "../test/helpers.ts";
import { decryptInstagramAccessToken } from "./token-crypto";

let setupPromise: Promise<{
  q: typeof import("./queries.ts");
}> | null = null;
let seq = 0;

async function setup() {
  if (!setupPromise) {
    process.env.TOKEN_ENCRYPTION_KEY = Buffer.from(
      Array.from({ length: 32 }, (_, index) => index),
    ).toString("base64");
    makeTestDb();
    setupPromise = import("./queries.ts");
  }
  return { q: await setupPromise, suffix: ++seq };
}

test("Instagram OAuth updates only credentials and auth method on the selected channel", async () => {
  const { q, suffix } = await setup();
  const channelId = q.createChannel({
    platform: "instagram",
    account_name: `OAuth target ${suffix}`,
    business_label: "Keep this label",
    timezone: "Asia/Aden",
    remote_account_id: "old-account",
    linked_page_id: "keep-page",
    access_token: "old-token",
    token_expires_at: "2026-12-01T00:00:00.000Z",
    requires_approval: true,
    color_hue: 142,
  });

  const result = q.connectInstagramChannel({
    channelId,
    remoteAccountId: `new-account-${suffix}`,
    accessToken: "new-long-lived-token",
    tokenExpiresAt: "2027-01-01T00:00:00.000Z",
    instagramAuthMethod: "instagram_login",
  });

  assert.equal(result, "connected");
  const channel = q.getChannel(channelId)!;
  assert.equal(channel.remote_account_id, `new-account-${suffix}`);
  assert.match(channel.access_token!, /^enc:v1:/);
  assert.equal(decryptInstagramAccessToken(channel.access_token!), "new-long-lived-token");
  assert.equal(channel.token_expires_at, "2027-01-01T00:00:00.000Z");
  assert.equal(channel.instagram_auth_method, "instagram_login");
  assert.equal(channel.account_name, `OAuth target ${suffix}`);
  assert.equal(channel.business_label, "Keep this label");
  assert.equal(channel.timezone, "Asia/Aden");
  assert.equal(channel.linked_page_id, "keep-page");
  assert.equal(channel.requires_approval, 1);
  assert.equal(channel.color_hue, 142);
});

test("Instagram OAuth refuses to attach an account already linked to another channel", async () => {
  const { q, suffix } = await setup();
  const existingId = q.createChannel({
    platform: "instagram",
    account_name: `Existing ${suffix}`,
    timezone: "UTC",
    remote_account_id: `duplicate-account-${suffix}`,
    access_token: "existing-token",
  });
  const targetId = q.createChannel({
    platform: "instagram",
    account_name: `Target ${suffix}`,
    timezone: "Asia/Aden",
    remote_account_id: "target-old-account",
    access_token: "target-old-token",
  });

  const result = q.connectInstagramChannel({
    channelId: targetId,
    remoteAccountId: `duplicate-account-${suffix}`,
    accessToken: "must-not-be-stored",
    tokenExpiresAt: "2027-01-01T00:00:00.000Z",
    instagramAuthMethod: "instagram_login",
  });

  assert.equal(result, "already_linked");
  assert.equal(q.getChannel(existingId)?.access_token, "existing-token");
  assert.equal(q.getChannel(targetId)?.remote_account_id, "target-old-account");
  assert.equal(q.getChannel(targetId)?.access_token, "target-old-token");
  assert.equal(q.getChannel(targetId)?.instagram_auth_method, "legacy");
});
