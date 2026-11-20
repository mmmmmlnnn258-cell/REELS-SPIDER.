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


test("Instagram OAuth connects different accounts to different selected channels", async () => {
  const { q, suffix } = await setup();
  const firstChannelId = q.createChannel({
    platform: "instagram",
    account_name: `First account ${suffix}`,
    timezone: "Asia/Aden",
  });
  const secondChannelId = q.createChannel({
    platform: "instagram",
    account_name: `Second account ${suffix}`,
    timezone: "Europe/London",
  });

  const first = q.connectInstagramChannel({
    channelId: firstChannelId,
    remoteAccountId: `instagram-user-one-${suffix}`,
    accessToken: `first-long-lived-token-${suffix}`,
    tokenExpiresAt: "2027-01-01T00:00:00.000Z",
    instagramAuthMethod: "instagram_login",
  });
  const second = q.connectInstagramChannel({
    channelId: secondChannelId,
    remoteAccountId: `instagram-user-two-${suffix}`,
    accessToken: `second-long-lived-token-${suffix}`,
    tokenExpiresAt: "2027-02-01T00:00:00.000Z",
    instagramAuthMethod: "instagram_login",
  });

  assert.equal(first, "connected");
  assert.equal(second, "connected");
  const firstSaved = q.getChannel(firstChannelId)!;
  const secondSaved = q.getChannel(secondChannelId)!;
  assert.equal(firstSaved.remote_account_id, `instagram-user-one-${suffix}`);
  assert.equal(secondSaved.remote_account_id, `instagram-user-two-${suffix}`);
  assert.equal(
    decryptInstagramAccessToken(firstSaved.access_token!),
    `first-long-lived-token-${suffix}`,
  );
  assert.equal(
    decryptInstagramAccessToken(secondSaved.access_token!),
    `second-long-lived-token-${suffix}`,
  );
  assert.equal(firstSaved.instagram_auth_method, "instagram_login");
  assert.equal(secondSaved.instagram_auth_method, "instagram_login");
  assert.equal(firstSaved.timezone, "Asia/Aden");
  assert.equal(secondSaved.timezone, "Europe/London");
});
