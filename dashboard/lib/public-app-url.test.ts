import { test } from "node:test";
import assert from "node:assert/strict";
import { publicAppOrigin } from "./public-app-url";

test("public app origin uses the configured HTTPS domain and strips paths", () => {
  process.env.PUBLIC_APP_URL = "https://scheduler.example.test/base/path";
  assert.equal(publicAppOrigin(), "https://scheduler.example.test");
  delete process.env.PUBLIC_APP_URL;
});

test("public app origin falls back to the deployed Railway domain", () => {
  delete process.env.PUBLIC_APP_URL;
  assert.equal(publicAppOrigin(), "https://reels-spider-production.up.railway.app");
});

test("public app origin rejects internal or non-HTTPS addresses", () => {
  for (const value of [
    "http://0.0.0.0:8080",
    "https://0.0.0.0",
    "http://scheduler.example.test",
    "http://localhost:3000",
  ]) {
    process.env.PUBLIC_APP_URL = value;
    assert.throws(() => publicAppOrigin(), /public HTTPS/);
  }
  delete process.env.PUBLIC_APP_URL;
});
