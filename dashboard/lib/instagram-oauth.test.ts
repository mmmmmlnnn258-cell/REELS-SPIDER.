import { test } from "node:test";
import assert from "node:assert/strict";
import {
  INSTAGRAM_SCOPES,
  createInstagramOAuthState,
  instagramAuthorizeUrl,
  instagramCallbackUri,
} from "./instagram-oauth";

test("Instagram OAuth state is cryptographically random and unique", () => {
  const first = createInstagramOAuthState();
  const second = createInstagramOAuthState();
  assert.match(first, /^[0-9a-f]{64}$/);
  assert.notEqual(first, second);
});

test("authorize URL uses Instagram Business Login and current permission names", () => {
  const url = new URL(
    instagramAuthorizeUrl({
      clientId: "app-id",
      redirectUri: "https://example.test/api/channels/instagram/callback",
      state: "random-state",
    }),
  );
  assert.equal(url.origin + url.pathname, "https://www.instagram.com/oauth/authorize");
  assert.equal(url.searchParams.get("client_id"), "app-id");
  assert.equal(url.searchParams.get("response_type"), "code");
  assert.equal(url.searchParams.get("state"), "random-state");
  assert.equal(url.searchParams.get("enable_fb_login"), "0");
  assert.equal(url.searchParams.get("scope"), INSTAGRAM_SCOPES.join(","));
  assert.ok(INSTAGRAM_SCOPES.includes("instagram_business_basic"));
  assert.ok(INSTAGRAM_SCOPES.includes("instagram_business_content_publish"));
  assert.ok(INSTAGRAM_SCOPES.includes("instagram_business_manage_comments"));
  assert.ok(INSTAGRAM_SCOPES.includes("instagram_business_manage_messages"));
});

test("callback URI is built on the application origin", () => {
  assert.equal(
    instagramCallbackUri("https://scheduler.example.test"),
    "https://scheduler.example.test/api/channels/instagram/callback",
  );
});
