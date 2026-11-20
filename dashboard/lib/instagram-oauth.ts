import { randomBytes } from "node:crypto";

/**
 * Instagram API with Instagram Login (Business Login) — not the legacy Facebook
 * Login/Page flow. This produces an Instagram User token for graph.instagram.com.
 */
export const INSTAGRAM_AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize";
export const INSTAGRAM_CODE_TOKEN_URL = "https://api.instagram.com/oauth/access_token";
export const INSTAGRAM_GRAPH_BASE = "https://graph.instagram.com";

export const INSTAGRAM_SCOPES = [
  "instagram_business_basic",
  "instagram_business_content_publish",
  "instagram_business_manage_comments",
  "instagram_business_manage_messages",
] as const;

export function createInstagramOAuthState(): string {
  return randomBytes(32).toString("hex");
}

export function instagramAuthorizeUrl(options: {
  clientId: string;
  redirectUri: string;
  state: string;
}): string {
  const url = new URL(INSTAGRAM_AUTHORIZE_URL);
  url.searchParams.set("client_id", options.clientId);
  url.searchParams.set("redirect_uri", options.redirectUri);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", INSTAGRAM_SCOPES.join(","));
  url.searchParams.set("state", options.state);
  url.searchParams.set("enable_fb_login", "false");
  return url.toString();
}

export function instagramCallbackUri(origin: string): string {
  return new URL("/api/channels/instagram/callback", origin).toString();
}
