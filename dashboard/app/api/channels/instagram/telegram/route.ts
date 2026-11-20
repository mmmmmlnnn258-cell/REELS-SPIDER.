import { createHash } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { instagramCredentials, instagramRedirectUri } from "@/lib/config";
import { createInstagramOAuthState, instagramAuthorizeUrl } from "@/lib/instagram-oauth";

export const runtime = "nodejs";
const STATE_COOKIE = "instagram_oauth_state";
const MODE_COOKIE = "instagram_oauth_mode";
const TICKET_COOKIE = "instagram_telegram_ticket";
const TTL = 600;

export async function GET(req: NextRequest) {
  const ticket = req.nextUrl.searchParams.get("ticket") || "";
  if (!/^[A-Za-z0-9_-]{32,128}$/.test(ticket)) {
    return NextResponse.json({ error: "رابط الربط غير صالح أو منتهي." }, { status: 400 });
  }

  const redirectUri = instagramRedirectUri();
  if (!redirectUri) {
    return NextResponse.json({ error: "Instagram callback URL is not configured." }, { status: 503 });
  }

  const digest = createHash("sha256").update(ticket, "utf8").digest("hex");
  const db = getDb();
  const now = new Date().toISOString();
  const claim = db.transaction(() => {
    const row = db.prepare(
      "SELECT id, platform FROM telegram_account_link_tickets WHERE ticket_hash=? AND status='pending' AND consumed_at IS NULL AND expires_at>?",
    ).get(digest, now) as { id: number; platform: string } | undefined;
    if (!row || row.platform !== "instagram") return null;
    const changed = db.prepare(
      "UPDATE telegram_account_link_tickets SET consumed_at=? WHERE id=? AND status='pending' AND consumed_at IS NULL AND expires_at>?",
    ).run(now, row.id, now).changes;
    return changed === 1 ? row : null;
  });
  const claimed = claim.immediate();
  if (!claimed) {
    return NextResponse.json({ error: "انتهت صلاحية رابط الربط أو تم استخدامه مسبقاً." }, { status: 410 });
  }

  // The one-time, expiring Telegram ticket is the authorization for this flow;
  // do not force a separate dashboard login before opening official Instagram Login.
  // Account linking is Instagram-only and independent from publishing channels.
  const mode = "instagram_login";
  const credentials = instagramCredentials();
  if (!credentials.clientId || !credentials.clientSecret) {
    db.prepare("UPDATE telegram_account_link_tickets SET status='failed', failure_reason=? WHERE ticket_hash=?")
      .run("بيانات تطبيق Meta غير مهيأة.", digest);
    return NextResponse.json({ error: "إعدادات تسجيل دخول Instagram غير مكتملة على الخادم." }, { status: 503 });
  }

  const state = createInstagramOAuthState();
  const authorizeUrl = instagramAuthorizeUrl({ clientId: credentials.clientId, redirectUri, state });
  const response = NextResponse.redirect(authorizeUrl);
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  const options = { httpOnly: true, secure: process.env.NODE_ENV === "production", sameSite: "lax" as const, path: "/", maxAge: TTL };
  response.cookies.set(STATE_COOKIE, state, options);
  response.cookies.set(MODE_COOKIE, mode, options);
  response.cookies.set(TICKET_COOKIE, digest, options);
  return response;
}
