import { test } from "node:test";
import assert from "node:assert/strict";
import {
  INSTAGRAM_SCOPES,
  createInstagramOAuthState,
  instagramAuthorizeUrl,
  instagramUnifiedAuthorizeUrl,
  instagramCallbackUri,
} from "./instagram-oauth";
import { instagramCredentials, instagramRedirectUri } from "./config";

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
  assert.equal(url.searchParams.get("enable_fb_login"), "false");
  assert.deepEqual(INSTAGRAM_SCOPES, [
    "instagram_business_basic",
    "instagram_business_content_publish",
    "instagram_business_manage_comments",
    "instagram_business_manage_messages",
  ]);
  assert.equal(url.searchParams.get("scope"), INSTAGRAM_SCOPES.join(","));
  assert.ok(INSTAGRAM_SCOPES.includes("instagram_business_basic"));
  assert.ok(INSTAGRAM_SCOPES.includes("instagram_business_content_publish"));
  assert.ok(INSTAGRAM_SCOPES.includes("instagram_business_manage_comments"));
  assert.ok(INSTAGRAM_SCOPES.includes("instagram_business_manage_messages"));
  assert.equal(INSTAGRAM_SCOPES.length, 4);
});

test("callback URI is built on the application origin", () => {
  assert.equal(
    instagramCallbackUri("https://scheduler.example.test"),
    "https://scheduler.example.test/api/channels/instagram/callback",
  );
});


test("configured Instagram redirect URI is HTTPS and pinned to the exact callback path", () => {
  process.env.INSTAGRAM_REDIRECT_URI =
    "https://scheduler.example.test/api/channels/instagram/callback";
  assert.equal(
    instagramRedirectUri(),
    "https://scheduler.example.test/api/channels/instagram/callback",
  );

  process.env.INSTAGRAM_REDIRECT_URI =
    "https://attacker.example.test/api/channels/instagram/callback";
  assert.equal(
    instagramRedirectUri(),
    "https://attacker.example.test/api/channels/instagram/callback",
  );

  process.env.INSTAGRAM_REDIRECT_URI =
    "http://scheduler.example.test/api/channels/instagram/callback";
  assert.equal(instagramRedirectUri(), "");

  process.env.INSTAGRAM_REDIRECT_URI =
    "https://scheduler.example.test/untrusted/path";
  assert.equal(instagramRedirectUri(), "");
});


test("Instagram OAuth uses its own app id and secret, never Meta app credentials", () => {
  process.env.META_APP_ID = "meta-app-id";
  process.env.META_APP_SECRET = "meta-app-secret";
  process.env.INSTAGRAM_APP_ID = "instagram-app-id";
  process.env.INSTAGRAM_APP_SECRET = "instagram-app-secret";
  assert.deepEqual(instagramCredentials(), {
    clientId: "instagram-app-id",
    clientSecret: "instagram-app-secret",
  });
  delete process.env.INSTAGRAM_APP_ID;
  delete process.env.INSTAGRAM_APP_SECRET;
  assert.deepEqual(instagramCredentials(), { clientId: "", clientSecret: "" });
});

test("Unified Login URL uses config_id and does not send classic scope parameters", () => {
  const url = new URL(instagramUnifiedAuthorizeUrl({
    clientId: "meta-app-id",
    configId: "business-login-config-id",
    redirectUri: "https://example.test/api/channels/instagram/callback",
    state: "random-state",
    graphVersion: "v26.0",
  }));
  assert.equal(url.origin + url.pathname, "https://www.facebook.com/v26.0/dialog/oauth");
  assert.equal(url.searchParams.get("client_id"), "meta-app-id");
  assert.equal(url.searchParams.get("config_id"), "business-login-config-id");
  assert.equal(url.searchParams.get("redirect_uri"), "https://example.test/api/channels/instagram/callback");
  assert.equal(url.searchParams.get("response_type"), "code");
  assert.equal(url.searchParams.get("override_default_response_type"), "true");
  assert.equal(url.searchParams.get("state"), "random-state");
  assert.equal(url.searchParams.has("scope"), false);
  assert.equal(url.searchParams.has("enable_fb_login"), false);
});
