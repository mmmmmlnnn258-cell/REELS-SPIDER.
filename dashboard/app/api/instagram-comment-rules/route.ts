import { NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/db";

export const runtime = "nodejs";

export async function GET() {
  const rules = getDb().prepare(
    `SELECT r.id, r.channel_id, r.media_id, r.keywords_json, r.link_url,
            r.private_reply_text, r.public_reply_text, r.enabled, r.last_polled_at,
            r.last_error, c.account_name
       FROM instagram_comment_rules r JOIN channels c ON c.id = r.channel_id
      ORDER BY r.created_at DESC, r.id DESC`
  ).all();
  return NextResponse.json({ rules });
}

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  if (!body || typeof body !== "object") return NextResponse.json({ error: "Invalid JSON." }, { status: 400 });
  const channelId = Number(body.channel_id);
  const mediaId = typeof body.media_id === "string" ? body.media_id.trim() : "";
  const keywords = Array.isArray(body.keywords)
    ? body.keywords.filter((v: unknown) => typeof v === "string").map((v: string) => v.trim()).filter(Boolean).slice(0, 30)
    : [];
  if (!Number.isInteger(channelId) || channelId < 1 || !mediaId) {
    return NextResponse.json({ error: "اختر الحساب وأدخل معرّف الريل." }, { status: 400 });
  }
  if (!keywords.length) return NextResponse.json({ error: "أدخل كلمة واحدة على الأقل." }, { status: 400 });
  const db = getDb();
  const channel = db.prepare("SELECT id, platform FROM channels WHERE id = ?").get(channelId) as { id: number; platform: string } | undefined;
  if (!channel || channel.platform !== "instagram") return NextResponse.json({ error: "الحساب المحدد ليس Instagram." }, { status: 400 });
  const link = typeof body.link_url === "string" ? body.link_url.trim() : "";
  if (link && !/^https:\/\//i.test(link)) return NextResponse.json({ error: "الرابط يجب أن يبدأ بـ https://." }, { status: 400 });
  const privateText = typeof body.private_reply_text === "string" ? body.private_reply_text.trim() : "";
  const publicText = typeof body.public_reply_text === "string" ? body.public_reply_text.trim() : "";
  if (!privateText && !publicText) return NextResponse.json({ error: "اكتب رسالة خاصة أو ردًا علنيًا." }, { status: 400 });
  const now = new Date().toISOString();
  db.prepare(
    `INSERT INTO instagram_comment_rules
      (channel_id, media_id, keywords_json, link_url, private_reply_text, public_reply_text, enabled, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
     ON CONFLICT(channel_id, media_id) DO UPDATE SET
       keywords_json=excluded.keywords_json, link_url=excluded.link_url,
       private_reply_text=excluded.private_reply_text, public_reply_text=excluded.public_reply_text,
       updated_at=excluded.updated_at`
  ).run(channelId, mediaId, JSON.stringify(keywords), link || null, privateText || null, publicText || null, now, now);
  const rule = db.prepare(
    `SELECT r.id, r.channel_id, r.media_id, r.keywords_json, r.link_url,
            r.private_reply_text, r.public_reply_text, r.enabled, r.last_polled_at,
            r.last_error, c.account_name
       FROM instagram_comment_rules r JOIN channels c ON c.id = r.channel_id
      WHERE r.channel_id=? AND r.media_id=?`
  ).get(channelId, mediaId) as Record<string, unknown>;
  return NextResponse.json({ rule: { ...rule, keywords } }, { status: 201 });
}

export async function PATCH(req: NextRequest) {
  const body = await req.json().catch(() => null);
  const id = Number(body?.id);
  if (!Number.isInteger(id) || id < 1 || typeof body?.enabled !== "boolean") {
    return NextResponse.json({ error: "Invalid rule update." }, { status: 400 });
  }
  const db = getDb();
  const result = db.prepare(
    "UPDATE instagram_comment_rules SET enabled=?, last_error=NULL, updated_at=? WHERE id=?"
  ).run(body.enabled ? 1 : 0, new Date().toISOString(), id);
  if (!result.changes) return NextResponse.json({ error: "Rule not found." }, { status: 404 });
  return NextResponse.json({ id, enabled: body.enabled });
}
