import { createHash } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { createInstagramConnectUrl, ensureZernioProfile, ZernioApiError } from "@/lib/zernio";

export const runtime = "nodejs";
const TICKET_PATTERN = /^[A-Za-z0-9_-]{32,128}$/;

function failTicket(ticketHash: string, reason: string) {
  try {
    getDb().prepare(
      "UPDATE telegram_account_link_tickets SET status='failed', failure_reason=? WHERE ticket_hash=? AND status='pending' AND platform='instagram'",
    ).run(reason.slice(0, 300), ticketHash);
  } catch {
    // Keep the browser response safe if the durable Telegram notification cannot be written.
  }
}

export async function GET(req: NextRequest) {
  const ticket = req.nextUrl.searchParams.get("ticket") || "";
  if (!TICKET_PATTERN.test(ticket)) {
    return NextResponse.json({ error: "رابط الربط غير صالح أو منتهي." }, { status: 400 });
  }
  const ticketHash = createHash("sha256").update(ticket, "utf8").digest("hex");
  const db = getDb();
  const now = new Date().toISOString();

  const claim = db.transaction(() => {
    const row = db.prepare(
      "SELECT id FROM telegram_account_link_tickets WHERE ticket_hash=? AND status='pending' AND consumed_at IS NULL AND expires_at>? AND platform='instagram'",
    ).get(ticketHash, now) as { id: number } | undefined;
    if (!row) return false;
    const changed = db.prepare(
      "UPDATE telegram_account_link_tickets SET consumed_at=? WHERE id=? AND status='pending' AND consumed_at IS NULL AND expires_at>?",
    ).run(now, row.id, now).changes;
    return changed === 1;
  });
  if (!claim.immediate()) {
    return NextResponse.json({ error: "انتهت صلاحية رابط الربط أو تم استخدامه مسبقاً." }, { status: 410 });
  }

  try {
    const profileId = await ensureZernioProfile();
    db.prepare(
      `INSERT INTO zernio_settings (id, profile_id, profile_name, created_at, updated_at)
       VALUES (1, ?, 'BLACK-SPIDER', ?, ?)
       ON CONFLICT(id) DO UPDATE SET profile_id=excluded.profile_id, updated_at=excluded.updated_at`,
    ).run(profileId, now, now);

    const callback = new URL("/api/channels/instagram/zernio/callback", req.nextUrl.origin);
    callback.searchParams.set("ticket", ticket);
    const authUrl = await createInstagramConnectUrl(profileId, callback.toString());
    const response = NextResponse.redirect(authUrl);
    response.headers.set("Cache-Control", "no-store");
    response.headers.set("Referrer-Policy", "no-referrer");
    return response;
  } catch (error) {
    const reason = error instanceof ZernioApiError
      ? error.message
      : "تعذر بدء تسجيل الدخول عبر Zernio.";
    failTicket(ticketHash, reason);
    return NextResponse.json({ error: reason }, { status: 503 });
  }
}
