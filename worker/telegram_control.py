"""Allow-listed Telegram control menu for safe, read-only operations and comment-rule toggles."""
from __future__ import annotations

from datetime import datetime, timezone


def _allowed(raw: str) -> set[str]:
    return {part.strip() for part in (raw or "").split(",") if part.strip()}


def _menu() -> dict:
    return {"inline_keyboard": [
        [{"text": "🚀 نشر الآن", "callback_data": "bs:publish"}],
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
    update_id = update.get("update_id")
    if update_id is not None and conn.execute(
        "SELECT 1 FROM telegram_ingest_updates WHERE update_id = ?", (int(update_id),)
    ).fetchone():
        return True
    callback = update.get("callback_query")
    if callback:
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        query_id = str(callback.get("id", ""))
        data = str(callback.get("data", ""))
        sender = callback.get("from") or {}
        sender_id = str(sender.get("id", ""))
        if (update_id is None or not chat_id or chat_id not in allowed
                or chat.get("type") != "private" or not sender_id or sender_id != chat_id):
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
                """SELECT pub.id, p.caption, pub.status, pub.scheduled_at
                     FROM publications pub JOIN posts p ON p.id = pub.post_id
                    WHERE pub.status IN ('scheduled','failed','pending_approval')
                    ORDER BY pub.scheduled_at ASC, pub.id ASC LIMIT 5"""
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
        if data == "bs:publish":
            rows = conn.execute(
                """SELECT pub.id, p.caption, pub.scheduled_at
                     FROM publications pub
                     JOIN posts p ON p.id = pub.post_id
                     JOIN channels c ON c.id = pub.channel_id
                    WHERE pub.status = 'scheduled' AND pub.is_held = 0
                      AND c.platform = 'instagram' AND c.is_active = 1
                      AND c.remote_account_id IS NOT NULL AND TRIM(c.remote_account_id) <> ''
                      AND c.access_token IS NOT NULL AND TRIM(c.access_token) <> ''
                    ORDER BY pub.scheduled_at ASC, pub.id ASC LIMIT 8"""
            ).fetchall()
            if not rows:
                _send(client, token, chat_id,
                      "لا توجد منشورات مجدولة جاهزة للنشر على قناة Instagram متصلة.", _menu())
                return True
            keyboard = {"inline_keyboard": [
                [{"text": f"🚀 نشر المنشور #{row['id']} الآن",
                  "callback_data": f"bs:publish:{row['id']}"}]
                for row in rows
            ] + [[{"text": "↩ القائمة", "callback_data": "bs:menu"}]]}
            lines = ["🚀 اختر منشوراً مجدولاً لتقديم موعده إلى الآن:"]
            for row in rows:
                caption = (row["caption"] or "بدون وصف").replace("\\n", " ")[:60]
                lines.append(f"#{row['id']} · {row['scheduled_at']}\\n{caption}")
            _send(client, token, chat_id, "\\n".join(lines), keyboard)
            return True
        if data.startswith("bs:publish:"):
            raw_id = data.removeprefix("bs:publish:")
            if not raw_id.isdigit() or len(raw_id) > 9:
                _send(client, token, chat_id, "طلب نشر غير صالح.", _menu())
                return True
            publication_id = int(raw_id)
            now = datetime.now(timezone.utc).isoformat()
            with conn:
                updated = conn.execute(
                    """UPDATE publications
                          SET scheduled_at = ?, next_retry_at = NULL, updated_at = ?
                        WHERE id = ? AND status = 'scheduled' AND is_held = 0
                          AND EXISTS (
                              SELECT 1 FROM channels c
                               WHERE c.id = publications.channel_id
                                 AND c.platform = 'instagram' AND c.is_active = 1
                                 AND c.remote_account_id IS NOT NULL
                                 AND TRIM(c.remote_account_id) <> ''
                                 AND c.access_token IS NOT NULL
                                 AND TRIM(c.access_token) <> ''
                          )""",
                    (now, now, publication_id),
                )
                if updated.rowcount != 1:
                    _send(client, token, chat_id,
                          "لم يتم تقديم المنشور: لم يعد مجدولاً أو أن قناة Instagram غير متصلة.", _menu())
                    return True
                if update_id is not None:
                    conn.execute(
                        """INSERT OR IGNORE INTO telegram_ingest_updates
                           (update_id, chat_id, status) VALUES (?, ?, 'ignored')""",
                        (int(update_id), chat_id),
                    )
            _send(client, token, chat_id,
                  f"تم تقديم المنشور #{publication_id} إلى قائمة النشر الآن. لم نؤكد النشر بعد؛ سيعالج العامل الطلب وفق DRY_RUN وKILL_SWITCH.",
                  _menu())
            return True
        if data == "bs:comments":
            rows = conn.execute(
                """SELECT r.id, r.media_id, r.enabled, c.account_name
                     FROM instagram_comment_rules r JOIN channels c ON c.id=r.channel_id
                    WHERE c.platform='instagram' AND c.is_active=1
                      AND c.remote_account_id IS NOT NULL AND TRIM(c.remote_account_id)<>''
                      AND c.access_token IS NOT NULL AND TRIM(c.access_token)<>''
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
                    WHERE r.id=? AND c.platform='instagram' AND c.is_active=1
                      AND c.remote_account_id IS NOT NULL AND TRIM(c.remote_account_id)<>''
                      AND c.access_token IS NOT NULL AND TRIM(c.access_token)<>''""",
                (rule_id,),
            ).fetchone()
            if row is None:
                _send(client, token, chat_id, "قاعدة التعليقات غير موجودة أو القناة غير نشطة.", _menu())
                return True
            new_value = 0 if row["enabled"] else 1
            with conn:
                conn.execute("UPDATE instagram_comment_rules SET enabled=?, updated_at=? WHERE id=?",
                             (new_value, datetime.now(timezone.utc).isoformat(), rule_id))
                if update_id is not None:
                    conn.execute(
                        """INSERT OR IGNORE INTO telegram_ingest_updates
                           (update_id, chat_id, status) VALUES (?, ?, 'ignored')""",
                        (int(update_id), chat_id),
                    )
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
    if not allowed:
        return False  # preserve the existing /start chat-ID discovery flow
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    sender = message.get("from") or {}
    sender_id = str(sender.get("id", ""))
    if (not chat_id or chat_id not in allowed or chat.get("type") != "private"
            or not sender_id or sender_id != chat_id):
        return True
    _send(client, token, chat_id, "🕷 BLACK SPIDER\nلوحة التحكم الرئيسية:", _menu())
    return True
