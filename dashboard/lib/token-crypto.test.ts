import assert from "node:assert/strict";
import { test } from "node:test";

import {
  decryptInstagramAccessToken,
  encryptInstagramAccessToken,
} from "./token-crypto";

const KEY = Buffer.from(Array.from({ length: 32 }, (_, index) => index)).toString("base64");

test("encrypts and decrypts with the shared versioned envelope", () => {
  process.env.TOKEN_ENCRYPTION_KEY = KEY;
  const encrypted = encryptInstagramAccessToken("IGAA-secret");
  assert.match(encrypted, /^enc:v1:/);
  assert.equal(decryptInstagramAccessToken(encrypted), "IGAA-secret");
});

test("decrypts the shared Python AES-GCM interoperability fixture", () => {
  process.env.TOKEN_ENCRYPTION_KEY = KEY;
  const fixture =
    "enc:v1:AAECAwQFBgcICQoL:DkWXWuiGsHT+Mrr5xIcMBO6zqlKZAysJSgJYj58f/W9q6/R01Gu8gusy";
  assert.equal(decryptInstagramAccessToken(fixture), "IGAA-cross-runtime-fixture");
});

test("keeps legacy plaintext readable and rejects tampering or missing key", () => {
  process.env.TOKEN_ENCRYPTION_KEY = KEY;
  assert.equal(decryptInstagramAccessToken("legacy-token"), "legacy-token");
  const encrypted = encryptInstagramAccessToken("IGAA-secret");
  const parts = encrypted.split(":");
  parts[3] = Buffer.from("tampered-ciphertext-and-tag").toString("base64");
  assert.throws(() => decryptInstagramAccessToken(parts.join(":")));
  delete process.env.TOKEN_ENCRYPTION_KEY;
  assert.throws(() => encryptInstagramAccessToken("new-token"));
});
