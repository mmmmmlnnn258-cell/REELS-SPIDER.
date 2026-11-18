"""Allow-listed Telegram control menu for safe, read-only operations and comment-rule toggles."""
from __future__ import annotations

from datetime import datetime, timezone


def _allowed(raw: str) -> set[str]:
    return {part.strip() for part in (raw or "").split(",") if part.strip()}


def _menu() -> dict:
    return {"inline_keyboard": [
        [{"text": "📊 حالة النظام", "callback_data": "bs:status"},
         {"text": "🗓 المنشورات القادمة", "callback_data": "bs:posts"}],
        [{"text": "💬 أتمتة التعليقات", "callback_data": "bs:comments"}],
    ]}


def _send(client, token: str, chat_id: str, text: str, keyboard: dict | None = None):
    return client.send_message(token, chat_id, text, reply_markup=keyboard)


def process_control_update(conn, config, client, update: dict) -> bool:
    """Handle /start, /menu and callback_query. Returns True when consumed.

    Authorization is checked against the chat ID before any database read or write.
    Callback data is treated as untrusted input and parsed with strict allow-listed forms.
    """
    token = config.telegram_bot_token
    allowed = _allowed(config.telegram_allowed_chat_ids)
    callback = update.get("callback_query")
    if callback:
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        query_id = str(callback.get("id", ""))
        data = str(callback.get("data", ""))
        if not chat_id or chat_id not in allowed or chat.get("type") != "private":
            if query_id:
                client.answer_callback_query(token, query_id, "غير مصرح")
            return True
        if query_id:
            client.answer_callback_query(token, query_id)
        if data == "bs:menu":
            _send(client, token, chat_id, "🕷 BLACK SPIDER\nاختر عملية:", _menu())
            return True
        if data == "bs:status":
            heartbeat = conn.execute("SELECT last_seen_at FROM worker_heartbeat WHERE id=1").fetchone()
            channels = conn.execute(
                "SELECT COUNT(*) FROM channels WHERE platform='instagram' AND is_active=1 "
                "AND remote_account_id IS NOT NULL AND TRIM(remote_account_id)<>'' "
                "AND access_token IS NOT NULL AND TRIM(access_token)<>''"
            ).fetchone()[0]
            queued = conn.execute("SELECT COUNT(*) FROM publications WHERE status='scheduled'").fetchone()[0]
            failed = conn.execute("SELECT COUNT(*) FROM publications WHERE status='failed'").fetchone()[0]
            stamp = heartbeat["last_seen_at"] if heartbeat else "غير متوفر"
            _send(client, token, chat_id,
                  f"📊 حالة النظام\nآخر نبضة Worker: {stamp}\nقنوات Instagram المتصلة والنشطة: {channels}\nمنشورات مجدولة: {queued}\nمنشورات فاشلة: {failed}",
                  _menu())
            return True
        if data == "bs:posts":
            rows = conn.execute(
                """SELECT p.id, p.caption, p.status, p.scheduled_at
                     FROM publications p WHERE p.status IN ('scheduled','failed','pending_approval')
                    ORDER BY p.scheduled_at ASC, p.id ASC LIMIT 5"""
            ).fetchall()
            if not rows:
                body = "🗓 لا توجد منشورات مجدولة أو فاشلة حالياً."
            else:
                lines = ["🗓 آخر المنشورات:"]
                for row in rows:
                    caption = (row["caption"] or "بدون وصف").replace("\\n", " ")[:70]
                    lines.append(f"#{row['id']} · {row['status']} · {row['scheduled_at']}\n{caption}")
                body = "\n".join(lines)
            _send(client, token, chat_id, body, _menu())
            return True
        if data == "bs:comments":
            rows = conn.execute(
                """SELECT r.id, r.media_id, r.enabled, c.account_name
                     FROM instagram_comment_rules r JOIN channels c ON c.id=r.channel_id
                    WHERE c.platform='instagram' AND c.is_active=1
                    ORDER BY r.id DESC LIMIT 8"""
            ).fetchall()
            if not rows:
                _send(client, token, chat_id, "لا توجد قواعد تعليقات مرتبطة بقنوات Instagram نشطة.", _menu())
                return True
            keyboard = {"inline_keyboard": [
                [{"text": f"{'🟢' if row['enabled'] else '⚪'} قاعدة #{row['id']} · {'إيقاف' if row['enabled'] else 'تشغيل'}",
                  "callback_data": f"bs:comment:{row['id']}"}]
                for row in rows
            ] + [[{"text": "↩ القائمة", "callback_data": "bs:menu"}]]}
            _send(client, token, chat_id, "💬 قواعد أتمتة التعليقات\nاضغط لتبديل حالة القاعدة:", keyboard)
            return True
        if data.startswith("bs:comment:"):
            raw_id = data.removeprefix("bs:comment:")
            if not raw_id.isdigit() or len(raw_id) > 9:
                _send(client, token, chat_id, "طلب غير صالح.", _menu())
                return True
            rule_id = int(raw_id)
            row = conn.execute(
                """SELECT r.enabled FROM instagram_comment_rules r
                     JOIN channels c ON c.id=r.channel_id
                    WHERE r.id=? AND c.platform='instagram' AND c.is_active=1""",
                (rule_id,),
            ).fetchone()
            if row is None:
                _send(client, token, chat_id, "قاعدة التعليقات غير موجودة أو القناة غير نشطة.", _menu())
                return True
            new_value = 0 if row["enabled"] else 1
            conn.execute("UPDATE instagram_comment_rules SET enabled=?, updated_at=? WHERE id=?",
                         (new_value, datetime.now(timezone.utc).isoformat(), rule_id))
            conn.commit()
            _send(client, token, chat_id,
                  f"{'تم تشغيل' if new_value else 'تم إيقاف'} أتمتة التعليقات للقاعدة #{rule_id}.",
                  _menu())
            return True
        _send(client, token, chat_id, "زر غير معروف. افتح القائمة من جديد.", _menu())
        return True

    message = update.get("message") or {}
    text = str(message.get("text") or "").strip().split("@", 1)[0]
    if text not in {"/start", "/menu"}:
        return False
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    if not chat_id or chat_id not in allowed or chat.get("type") != "private":
        return True
    _send(client, token, chat_id, "🕷 BLACK SPIDER\nلوحة التحكم الرئيسية:", _menu())
    return True
