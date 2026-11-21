import { createHash } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/db";
import { getInstagramAccount, ZernioApiError } from "@/lib/zernio";

export const runtime = "nodejs";
const TICKET_PATTERN = /^[A-Za-z0-9_-]{32,128}$/;

function returnToChannels(req: NextRequest, result: Record<string, string>) {
  const url = new URL("/channels", req.nextUrl.origin);
  for (const [key, value] of Object.entries(result)) url.searchParams.set(key, value);
  const response = NextResponse.redirect(url);
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}

function fail(ticketHash: string, reason: string) {
  getDb().prepare(
    "UPDATE telegram_account_link_tickets SET status='failed', failure_reason=? WHERE ticket_hash=? AND status='pending' AND consumed_at IS NOT NULL AND platform='instagram'",
  ).run(reason.slice(0, 300), ticketHash);
}

export async function GET(req: NextRequest) {
  const ticket = req.nextUrl.searchParams.get("ticket") || "";
  if (!TICKET_PATTERN.test(ticket)) {
    return returnToChannels(req, { zernio_error: "رابط الربط غير صالح أو منتهي." });
  }
  const ticketHash = createHash("sha256").update(ticket, "utf8").digest("hex");
  const db = getDb();
  const now = new Date().toISOString();
  const ticketRow = db.prepare(
    "SELECT telegram_chat_id, telegram_user_id FROM telegram_account_link_tickets WHERE ticket_hash=? AND status='pending' AND consumed_at IS NOT NULL AND expires_at>? AND platform='instagram'",
  ).get(ticketHash, now) as { telegram_chat_id: string; telegram_user_id: string } | undefined;
  if (!ticketRow) {
    return returnToChannels(req, { zernio_error: "انتهت صلاحية جلسة الربط أو تم استخدامها." });
  }

  const providerError = req.nextUrl.searchParams.get("error") ||
    req.nextUrl.searchParams.get("error_description");
  if (providerError) {
    const reason = "ألغيت عملية تسجيل الدخول أو رفض Instagram التفويض.";
    fail(ticketHash, reason);
    return returnToChannels(req, { zernio_error: reason });
  }

  const connected = req.nextUrl.searchParams.get("connected");
  const profileId = req.nextUrl.searchParams.get("profileId") || "";
  const accountId = req.nextUrl.searchParams.get("accountId") || "";
  if (connected !== "instagram" || !profileId || !accountId) {
    const reason = "لم تكتمل موافقة Instagram لدى Zernio.";
    fail(ticketHash, reason);
    return returnToChannels(req, { zernio_error: reason });
  }

  try {
    const configured = db.prepare("SELECT profile_id FROM zernio_settings WHERE id=1").get() as
      { profile_id: string } | undefined;
    if (!configured || configured.profile_id !== profileId) {
      throw new ZernioApiError("ملف Zernio لا يطابق جلسة الربط الحالية.");
    }
    const account = await getInstagramAccount(profileId, accountId);
    const candidates = db.prepare(
      `SELECT id FROM channels
        WHERE platform='instagram' AND is_active=1
          AND (zernio_account_id IS NULL OR TRIM(zernio_account_id)='')
          AND (remote_account_id IS NULL OR TRIM(remote_account_id)='')
          AND (access_token IS NULL OR TRIM(access_token)='')
        ORDER BY id`,
    ).all() as Array<{ id: number }>;

    const existing = db.prepare(
      "SELECT id FROM channels WHERE zernio_account_id=?",
    ).get(accountId) as { id: number } | undefined;
    if (existing) {
      db.prepare(
        "UPDATE telegram_account_link_tickets SET status='connected', result_account_name=?, result_account_id=?, failure_reason=NULL WHERE ticket_hash=?",
      ).run(account.username, accountId, ticketHash);
      return returnToChannels(req, { zernio_connected: "1", zernio_account: account.username });
    }
    if (candidates.length !== 1) {
      const reason = candidates.length === 0
        ? "تم تفويض الحساب في Zernio، لكن لم نجد قناة Instagram واحدة غير مرتبطة لربطه بها."
        : "تم تفويض الحساب في Zernio، لكن توجد عدة قنوات Instagram غير مرتبطة. يلزم تحديد القناة قبل إكمال الربط.";
      fail(ticketHash, reason);
      return returnToChannels(req, { zernio_error: reason });
    }

    const linked = db.prepare(
      `UPDATE channels
          SET zernio_account_id=?, zernio_profile_id=?, account_name=?, updated_at=?
        WHERE id=? AND zernio_account_id IS NULL
          AND (remote_account_id IS NULL OR TRIM(remote_account_id)='')
          AND (access_token IS NULL OR TRIM(access_token)='')`,
    ).run(accountId, profileId, account.username, now, candidates[0].id).changes;
    if (linked !== 1) {
      throw new ZernioApiError("تغيرت القناة أثناء الربط؛ لم يتم تعديل أي قناة.");
    }
    db.prepare(
      "UPDATE telegram_account_link_tickets SET status='connected', result_account_name=?, result_account_id=?, failure_reason=NULL WHERE ticket_hash=?",
    ).run(account.username, accountId, ticketHash);
    return returnToChannels(req, { zernio_connected: "1", zernio_account: account.username });
  } catch (error) {
    const reason = error instanceof ZernioApiError
      ? error.message
      : "تعذر التحقق من حساب Instagram أو ربطه بالقناة.";
    fail(ticketHash, reason);
    return returnToChannels(req, { zernio_error: reason });
  }
}
