"""Allow-listed Telegram control menu for safe, read-only operations and comment-rule toggles."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _allowed(raw: str) -> set[str]:
    return {part.strip() for part in (raw or "").split(",") if part.strip()}


def _menu() -> dict:
    return {"inline_keyboard": [
        [{"text": "🚀 نشر الآن", "callback_data": "bs:publish"}],
        [{"text": "📅 إدارة الجدولة", "callback_data": "bs:schedule"}],
        [{"text": "📊 حالة النظام", "callback_data": "bs:status"},
         {"text": "🗓 المنشورات القادمة", "callback_data": "bs:posts"}],
        [{"text": "💬 أتمتة التعليقات", "callback_data": "bs:comments"}],
    ]}


def _send(client, token: str, chat_id: str, text: str, keyboard: dict | None = None):
    return client.send_message(token, chat_id, text, reply_markup=keyboard)


def _local_stamp(value: str, timezone_name: str) -> str:
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        zone = ZoneInfo(timezone_name)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(zone).strftime("%Y-%m-%d %H:%M")
    except (ValueError, ZoneInfoNotFoundError):
        return value


def _parse_local_schedule(value: str, timezone_name: str) -> datetime:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", value):
        raise ValueError("اكتب التاريخ والوقت بهذا الشكل بالضبط: YYYY-MM-DD HH:MM")
    try:
        local = datetime.strptime(value, "%Y-%m-%d %H:%M")
        zone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("التاريخ أو المنطقة الزمنية غير صالحة. أرسل موعداً صحيحاً.") from None
    first = local.replace(tzinfo=zone, fold=0)
    round_trip = first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
    if round_trip != local:
        raise ValueError("هذا الوقت غير موجود في المنطقة الزمنية بسبب تغيير الساعة. اختر وقتاً آخر.")
    second = local.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        second_round_trip = second.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if second_round_trip == local:
            raise ValueError("هذا الوقت يتكرر بسبب تغيير الساعة. اختر وقتاً غير ملتبس.")
    result = first.astimezone(timezone.utc)
    if result <= datetime.now(timezone.utc):
        raise ValueError("الموعد الجديد يجب أن يكون في المستقبل.")
    return result


def _schedule_rows(conn):
    return conn.execute(
        """SELECT pub.id, pub.status, pub.scheduled_at, pub.is_held,
                  p.caption, p.post_type, c.account_name, c.timezone
             FROM publications pub
             JOIN posts p ON p.id = pub.post_id
             JOIN channels c ON c.id = pub.channel_id
            WHERE pub.status IN ('scheduled', 'pending_approval')
            ORDER BY pub.scheduled_at ASC, pub.id ASC LIMIT 8"""
    ).fetchall()


def _schedule_list(conn, client, token: str, chat_id: str, page: int = 0):
    total = conn.execute(
        "SELECT COUNT(*) FROM publications WHERE status IN ('scheduled', 'pending_approval')"
    ).fetchone()[0]
    page_size = 8
    page_count = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, page_count - 1))
    rows = conn.execute(
        """SELECT pub.id, pub.status, pub.scheduled_at, pub.is_held,
                  p.caption, p.post_type, c.account_name, c.timezone
             FROM publications pub
             JOIN posts p ON p.id = pub.post_id
             JOIN channels c ON c.id = pub.channel_id
            WHERE pub.status IN ('scheduled', 'pending_approval')
            ORDER BY pub.scheduled_at ASC, pub.id ASC LIMIT ? OFFSET ?""",
        (page_size, page * page_size),
    ).fetchall()
    if not rows:
        _send(client, token, chat_id, "📅 لا توجد منشورات مجدولة حالياً.", {
            "inline_keyboard": [[{"text": "↩ القائمة", "callback_data": "bs:menu"}]]
        })
        return
    lines = [f"📅 المنشورات المجدولة الحالية (صفحة {page + 1}/{page_count}):"]
    keyboard = []
    for row in rows:
        caption = (row["caption"] or "بدون وصف").replace("\\\\n", " ")[:48]
        local_time = _local_stamp(row["scheduled_at"], row["timezone"])
        state = "موقوف مؤقتاً" if row["is_held"] else (
            "بانتظار الموافقة" if row["status"] == "pending_approval" else "مجدول"
        )
        lines.append(f"#{row['id']} · {local_time} ({row['timezone']}) · {state}\\n{caption}")
        keyboard.append([{"text": f"تفاصيل المنشور #{row['id']}",
                          "callback_data": f"bs:schedule:view:{row['id']}:{page}"}])
    navigation = []
    if page > 0:
        navigation.append({"text": "⬅ السابقة", "callback_data": f"bs:schedule:page:{page - 1}"})
    if page + 1 < page_count:
        navigation.append({"text": "التالية ➡", "callback_data": f"bs:schedule:page:{page + 1}"})
    if navigation:
        keyboard.append(navigation)
    keyboard.append([{"text": "↩ القائمة", "callback_data": "bs:menu"}])
    _send(client, token, chat_id, "\\n".join(lines), {"inline_keyboard": keyboard})

def _schedule_detail(conn, client, token: str, chat_id: str, publication_id: int, page: int = 0):
    row = conn.execute(
        """SELECT pub.id, pub.status, pub.scheduled_at, pub.is_held, pub.post_id,
                  p.caption, p.post_type, c.account_name, c.timezone
             FROM publications pub
             JOIN posts p ON p.id = pub.post_id
             JOIN channels c ON c.id = pub.channel_id
            WHERE pub.id = ? AND pub.status IN ('scheduled', 'pending_approval')""",
        (publication_id,),
    ).fetchone()
    if row is None:
        _send(client, token, chat_id, "هذا الموعد لم يعد مجدولاً.", {
            "inline_keyboard": [[{"text": "↩ إدارة الجدولة", "callback_data": "bs:schedule"}]]
        })
        return
    assets = conn.execute(
        """SELECT a.original_filename, a.media_kind, a.byte_size
             FROM post_assets pa JOIN assets a ON a.id = pa.asset_id
            WHERE pa.post_id = ? ORDER BY pa.sort_order, pa.id""",
        (row["post_id"],),
    ).fetchall()
    caption = (row["caption"] or "بدون وصف").strip()[:1200]
    media = [f"• {a['original_filename'] or a['media_kind']} ({a['media_kind']})" for a in assets]
    status = "موقوف مؤقتاً" if row["is_held"] else (
        "بانتظار الموافقة" if row["status"] == "pending_approval" else "مجدول"
    )
    body = (
        f"📄 تفاصيل المنشور #{row['id']}\\n"
        f"القناة: {row['account_name']}\\n"
        f"النوع: {row['post_type']}\\n"
        f"الحالة: {status}\n"
        f"الموعد: {_local_stamp(row['scheduled_at'], row['timezone'])} ({row['timezone']})\\n"
        f"الوسائط: {len(assets)} ملف\n"
        f"الوصف: {caption}\\n"
        + ("الملفات:\n" + "\n".join(media) if media else "لا توجد ملفات وسائط مرتبطة.")
    )
    keyboard = {"inline_keyboard": [
        [{"text": "🕒 تأجيل الموعد", "callback_data": f"bs:schedule:reschedule:{publication_id}"}],
        [{"text": "📝 إلغاء الجدولة وتحويل لمسودة", "callback_data": f"bs:schedule:cancel:{publication_id}"}],
        [{"text": "↩ قائمة الجدولة", "callback_data": f"bs:schedule:page:{page}"}],
    ]}
    _send(client, token, chat_id, body, keyboard)


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

        if data == "bs:schedule":
            _schedule_list(conn, client, token, chat_id)
            return True
        if data.startswith("bs:schedule:page:"):
            raw_page = data.removeprefix("bs:schedule:page:")
            if not raw_page.isdigit() or len(raw_page) > 5:
                _send(client, token, chat_id, "رقم صفحة غير صالح.", _menu())
                return True
            _schedule_list(conn, client, token, chat_id, int(raw_page))
            return True
        if data.startswith("bs:schedule:view:"):
            parts = data.removeprefix("bs:schedule:view:").split(":")
            if len(parts) not in {1, 2} or not parts[0].isdigit() or len(parts[0]) > 9:
                _send(client, token, chat_id, "رقم منشور غير صالح.", _menu())
                return True
            if len(parts) == 2 and (not parts[1].isdigit() or len(parts[1]) > 5):
                _send(client, token, chat_id, "رقم صفحة غير صالح.", _menu())
                return True
            page = int(parts[1]) if len(parts) == 2 else 0
            _schedule_detail(conn, client, token, chat_id, int(parts[0]), page)
            return True
        if data.startswith("bs:schedule:reschedule:"):
            raw_id = data.removeprefix("bs:schedule:reschedule:")
            if not raw_id.isdigit() or len(raw_id) > 9:
                _send(client, token, chat_id, "رقم منشور غير صالح.", _menu())
                return True
            publication_id = int(raw_id)
            row = conn.execute(
                """SELECT c.timezone FROM publications pub
                     JOIN channels c ON c.id = pub.channel_id
                    WHERE pub.id = ? AND pub.status IN ('scheduled', 'pending_approval')""",
                (publication_id,),
            ).fetchone()
            if row is None:
                _send(client, token, chat_id, "هذا الموعد لم يعد قابلاً للتأجيل.", _menu())
                return True
            now = datetime.now(timezone.utc).isoformat()
            with conn:
                conn.execute(
                    """INSERT INTO telegram_schedule_sessions(chat_id, publication_id, timezone, created_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(chat_id) DO UPDATE SET
                         publication_id=excluded.publication_id,
                         timezone=excluded.timezone, created_at=excluded.created_at""",
                    (chat_id, publication_id, row["timezone"], now),
                )
                conn.execute(
                    """INSERT OR IGNORE INTO telegram_ingest_updates
                       (update_id, chat_id, status) VALUES (?, ?, 'ignored')""",
                    (int(update_id), chat_id),
                )
            _send(client, token, chat_id,
                  f"أرسل الموعد الجديد بالتوقيت المحلي للقناة ({row['timezone']}) بهذا الشكل:\nYYYY-MM-DD HH:MM\nمثال: 2026-12-01 18:30\nلديك 20 دقيقة لإرسال الموعد. أرسل /menu للإلغاء.",
                  {"inline_keyboard": [[{"text": "↩ إلغاء التأجيل", "callback_data": "bs:schedule"}]]})
            return True
        if data.startswith("bs:schedule:cancel:"):
            raw_id = data.removeprefix("bs:schedule:cancel:")
            if not raw_id.isdigit() or len(raw_id) > 9:
                _send(client, token, chat_id, "رقم منشور غير صالح.", _menu())
                return True
            publication_id = int(raw_id)
            now = datetime.now(timezone.utc).isoformat()
            with conn:
                changed = conn.execute(
                    """UPDATE publications
                          SET status='canceled', next_retry_at=NULL, updated_at=?
                        WHERE id=? AND status IN ('scheduled', 'pending_approval')""",
                    (now, publication_id),
                )
                if changed.rowcount == 1:
                    post_id = conn.execute(
                        "SELECT post_id FROM publications WHERE id=?", (publication_id,)
                    ).fetchone()["post_id"]
                    conn.execute(
                        """UPDATE posts SET status='draft', updated_at=?
                            WHERE id=? AND NOT EXISTS (
                              SELECT 1 FROM publications other
                               WHERE other.post_id=posts.id
                                 AND other.status IN ('scheduled','pending_approval','publishing','posted')
                            )""",
                        (now, post_id),
                    )
                conn.execute(
                    """INSERT OR IGNORE INTO telegram_ingest_updates
                       (update_id, chat_id, status) VALUES (?, ?, 'ignored')""",
                    (int(update_id), chat_id),
                )
                conn.execute("DELETE FROM telegram_schedule_sessions WHERE chat_id=?", (chat_id,))
            if changed.rowcount == 1:
                _send(client, token, chat_id,
                      f"تم إلغاء جدولة المنشور #{publication_id}. لم نحذف المنشور أو أي ملف وسائط. إذا لم تكن له مواعيد أخرى نشطة فستصبح حالته مسودة.",
                      {"inline_keyboard": [[{"text": "📅 إدارة الجدولة", "callback_data": "bs:schedule"}],
                                           [{"text": "↩ القائمة", "callback_data": "bs:menu"}]]})
            else:
                _send(client, token, chat_id, "لم يتم الإلغاء: الموعد لم يعد مجدولاً أو ينتظر الموافقة.", _menu())
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
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    sender = message.get("from") or {}
    sender_id = str(sender.get("id", ""))
    if chat_id and chat_id in allowed and chat.get("type") == "private" and sender_id == chat_id:
        session = conn.execute(
            "SELECT publication_id, timezone, created_at FROM telegram_schedule_sessions WHERE chat_id=?",
            (chat_id,),
        ).fetchone()
        if session:
            now_dt = datetime.now(timezone.utc)
            try:
                created = datetime.fromisoformat(session["created_at"].replace("Z", "+00:00"))
            except ValueError:
                created = now_dt - timedelta(hours=1)
            if now_dt - created > timedelta(minutes=20):
                with conn:
                    conn.execute("DELETE FROM telegram_schedule_sessions WHERE chat_id=?", (chat_id,))
                _send(client, token, chat_id, "انتهت مهلة التأجيل. افتح إدارة الجدولة وابدأ من جديد.", _menu())
                return True
            raw_text = str(message.get("text") or "").strip()
            if raw_text in {"/menu", "/start"}:
                with conn:
                    conn.execute("DELETE FROM telegram_schedule_sessions WHERE chat_id=?", (chat_id,))
                _send(client, token, chat_id, "تم إلغاء إدخال الموعد.", _menu())
                return True
            try:
                scheduled_utc = _parse_local_schedule(raw_text, session["timezone"])
            except ValueError as exc:
                _send(client, token, chat_id, str(exc) + "\nحاول مرة أخرى، أو أرسل /menu للإلغاء.")
                return True
            now = now_dt.isoformat()
            with conn:
                changed = conn.execute(
                    """UPDATE publications
                          SET scheduled_at=?, next_retry_at=NULL, updated_at=?
                        WHERE id=? AND status IN ('scheduled','pending_approval')""",
                    (scheduled_utc.isoformat(), now, int(session["publication_id"])),
                )
                conn.execute("DELETE FROM telegram_schedule_sessions WHERE chat_id=?", (chat_id,))
                conn.execute(
                    """INSERT OR IGNORE INTO telegram_ingest_updates
                       (update_id, chat_id, status) VALUES (?, ?, 'ignored')""",
                    (int(update_id), chat_id),
                )
            if changed.rowcount == 1:
                _send(client, token, chat_id,
                      f"تم تأجيل المنشور #{session['publication_id']} إلى {_local_stamp(scheduled_utc.isoformat(), session['timezone'])} ({session['timezone']}). لم يتغير المنشور أو ملفاته.",
                      {"inline_keyboard": [[{"text": "📅 إدارة الجدولة", "callback_data": "bs:schedule"}],
                                           [{"text": "↩ القائمة", "callback_data": "bs:menu"}]]})
            else:
                _send(client, token, chat_id, "لم يتم تغيير الموعد؛ المنشور لم يعد مجدولاً أو ينتظر الموافقة.", _menu())
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
