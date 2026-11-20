import "server-only";
import { config } from "./config";
import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";

const PREFIX = "enc:v1:";
const AAD = Buffer.from("reels-spider:channels:instagram:access_token:v1", "utf8");

function encryptionKey(): Buffer {
  const encoded = process.env.TOKEN_ENCRYPTION_KEY ?? config.tokenEncryptionKey;
  let key: Buffer;
  try {
    key = Buffer.from(encoded, "base64");
  } catch {
    throw new Error("TOKEN_ENCRYPTION_KEY must be base64 for exactly 32 bytes");
  }
  if (key.length !== 32 || key.toString("base64") !== encoded) {
    throw new Error("TOKEN_ENCRYPTION_KEY must be canonical base64 for exactly 32 bytes");
  }
  return key;
}

export function encryptInstagramAccessToken(token: string): string {
  if (token.startsWith(PREFIX)) {
    decryptInstagramAccessToken(token);
    return token;
  }
  const nonce = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", encryptionKey(), nonce);
  cipher.setAAD(AAD);
  const ciphertext = Buffer.concat([cipher.update(token, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return `${PREFIX}${nonce.toString("base64")}:${Buffer.concat([ciphertext, tag]).toString("base64")}`;
}

export function decryptInstagramAccessToken(value: string): string {
  if (!value.startsWith(PREFIX)) return value; // pre-encryption legacy rows
  const parts = value.split(":");
  if (parts.length !== 4 || parts[0] !== "enc" || parts[1] !== "v1") {
    throw new Error("Unsupported encrypted token envelope");
  }
  const nonce = Buffer.from(parts[2], "base64");
  const sealed = Buffer.from(parts[3], "base64");
  if (nonce.length !== 12 || sealed.length < 16 ||
      nonce.toString("base64") !== parts[2] || sealed.toString("base64") !== parts[3]) {
    throw new Error("Malformed encrypted token envelope");
  }
  try {
    const decipher = createDecipheriv("aes-256-gcm", encryptionKey(), nonce);
    decipher.setAAD(AAD);
    decipher.setAuthTag(sealed.subarray(sealed.length - 16));
    return Buffer.concat([
      decipher.update(sealed.subarray(0, sealed.length - 16)),
      decipher.final(),
    ]).toString("utf8");
  } catch {
    throw new Error("Unable to decrypt Instagram access token");
  }
}
