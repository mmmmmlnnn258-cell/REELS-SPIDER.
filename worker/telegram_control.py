"""Allow-listed Telegram control menu for safe, read-only operations and comment-rule toggles."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import urlencode
from .telegram_link_tickets import create_link_ticket
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _allowed(raw: str) -> set[str]:
    return {part.strip() for part in (raw or "").split(",") if part.strip()}


def _menu() -> dict:
    return {"inline_keyboard": [
        [{"text": "🚀 نشر الآن", "callback_data": "bs:publish"}],
        [{"text": "📅 إدارة الجدولة", "callback_data": "bs:schedule"}],
        [{"text": "👤 الحسابات المرتبطة", "callback_data": "bs:accounts"}],
        [{"text": "📊 حالة النظام", "callback_data": "bs:status"},
         {"text": "🗓 المنشورات القادمة", "callback_data": "bs:posts"}],
        [{"text": "💬 إدارة التعليقات", "callback_data": "bs:comments"},
         {"text": "✉️ الرسائل الخاصة", "callback_data": "bs:dms"}],
        [{"text": "⏯ إيقاف / استئناف النشر", "callback_data": "bs:publishing_control"}],
        [{"text": "📋 سجل العمليات", "callback_data": "bs:operations"}],
    ]}


def _send(client, token: str, chat_id: str, text: str, keyboard: dict | None = None):
    return client.send_message(token, chat_id, text, reply_markup=keyboard)


def _session_expired(created_at: str, minutes: int = 20) -> bool:
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    return datetime.now(timezone.utc) - created > timedelta(minutes=minutes)


def _instagram_dm_window(messages: list[dict], recipient_id: str,
                          now: datetime) -> tuple[bool, str]:
    """Require a recent inbound customer message before using Instagram Send API.

    A private reply to a comment is a separate one-message path; normal conversation
    replies are limited to 24 hours after the recipient's latest inbound message.
    Fail closed when Meta does not provide a trustworthy inbound timestamp.
    """
    inbound = []
    for message in messages:
        sender = message.get("from") or {}
        if str(sender.get("id") or "") != str(recipient_id):
            continue
        raw = message.get("created_time")
        if not raw:
            continue
        try:
            stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        inbound.append(stamp.astimezone(timezone.utc))

    if not inbound:
        return False, "لم تصل رسالة حديثة موثقة من العميل؛ لم يتم إرسال الرد."
    latest = max(inbound)
    age = now.astimezone(timezone.utc) - latest
    if age < timedelta(minutes=-5):
        return False, "وقت آخر رسالة من العميل غير صالح؛ لم يتم إرسال الرد."
    if age > timedelta(hours=24):
        return False, "انتهت مهلة Meta البالغة 24 ساعة منذ آخر رسالة من العميل؛ اطلب منه مراسلتك مجدداً."
    return True, ""


def _zernio_dm_window(messages: list[dict], now: datetime) -> tuple[bool, str]:
    """Fail closed unless Zernio confirms an inbound Instagram message in 24 hours."""
    inbound = []
    for message in messages:
        if message.get("direction") != "incoming":
            continue
        raw = message.get("createdAt")
        if not raw:
            continue
        try:
            stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        inbound.append(stamp.astimezone(timezone.utc))
    if not inbound:
        return False, "لم تصل رسالة واردة حديثة موثقة؛ لم يتم إرسال الرد."
    age = now.astimezone(timezone.utc) - max(inbound)
    if age < timedelta(minutes=-5):
        return False, "وقت آخر رسالة واردة غير صالح؛ لم يتم إرسال الرد."
    if age > timedelta(hours=24):
        return False, "انتهت مهلة Instagram البالغة 24 ساعة منذ آخر رسالة واردة؛ اطلب من العميل مراسلتك مجدداً."
    return True, ""


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
        caption = (row["caption"] or "بدون وصف").replace("\n", " ")[:48]
        local_time = _local_stamp(row["scheduled_at"], row["timezone"])
        state = "موقوف مؤقتاً" if row["is_held"] else (
            "بانتظار الموافقة" if row["status"] == "pending_approval" else "مجدول"
        )
        lines.append(f"#{row['id']} · {local_time} ({row['timezone']}) · {state}\n{caption}")
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
    _send(client, token, chat_id, "\n".join(lines), {"inline_keyboard": keyboard})

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
        f"📄 تفاصيل المنشور #{row['id']}\n"
        f"القناة: {row['account_name']}\n"
        f"النوع: {row['post_type']}\n"
        f"الحالة: {status}\n"
        f"الموعد: {_local_stamp(row['scheduled_at'], row['timezone'])} ({row['timezone']})\n"
        f"الوسائط: {len(assets)} ملف\n"
        f"الوصف: {caption}\n"
        + ("الملفات:\n" + "\n".join(media) if media else "لا توجد ملفات وسائط مرتبطة.")
    )
    keyboard = {"inline_keyboard": [
        [{"text": "🕒 تأجيل الموعد", "callback_data": f"bs:schedule:reschedule:{publication_id}"}],
        [{"text": "📝 إلغاء الجدولة وتحويل لمسودة", "callback_data": f"bs:schedule:cancel:{publication_id}"}],
        [{"text": "↩ قائمة الجدولة", "callback_data": f"bs:schedule:page:{page}"}],
    ]}
    _send(client, token, chat_id, body, keyboard)



def _uses_instagram_login(channel, graph) -> bool:
    """Use the channel's explicit login method; only legacy rows infer from their client."""
    keys = channel.keys() if hasattr(channel, "keys") else ()
    method = channel["instagram_auth_method"] if "instagram_auth_method" in keys else "legacy"
    if method == "instagram_login":
        return True
    if method == "facebook_login":
        return False
    return "graph.instagram.com" in getattr(graph, "base", "")


def _messaging_permission_hint(channel, graph) -> str:
    if _uses_instagram_login(channel, graph):
        return "صلاحيات Instagram Login المطلوبة: instagram_business_basic وinstagram_business_manage_messages."
    return ("صلاحيات Facebook Login المطلوبة: instagram_basic وinstagram_manage_messages "
            "وpages_manage_metadata، مع Page Access Token وصلاحية MESSAGING على الصفحة.")


def _comment_permission_hint(channel, graph) -> str:
    if _uses_instagram_login(channel, graph):
        return "تحقق من صلاحية instagram_business_manage_comments."
    return "تحقق من صلاحية instagram_manage_comments."


def _connected_ig_channels(conn):
    return conn.execute("""SELECT id, platform, account_name, remote_account_id, linked_page_id, access_token,
               instagram_auth_method, zernio_account_id, zernio_profile_id
        FROM channels WHERE platform='instagram' AND is_active=1
        AND (
          (remote_account_id IS NOT NULL AND TRIM(remote_account_id)<>''
           AND access_token IS NOT NULL AND TRIM(access_token)<>'')
          OR (zernio_account_id IS NOT NULL AND TRIM(zernio_account_id)<>'')
        ) ORDER BY id""").fetchall()


def _comment_rule_detail(conn, client, token, chat_id, rule_id):
    row = conn.execute("""SELECT r.*, c.account_name FROM instagram_comment_rules r
        JOIN channels c ON c.id=r.channel_id WHERE r.id=? AND c.platform='instagram'
        AND c.is_active=1 AND (
          (c.zernio_account_id IS NOT NULL AND TRIM(c.zernio_account_id)<>'')
          OR (c.remote_account_id IS NOT NULL AND c.access_token IS NOT NULL)
        )""", (rule_id,)).fetchone()
    if row is None:
        _send(client, token, chat_id, "قاعدة التعليقات غير موجودة أو القناة غير متصلة.", _menu())
        return
    try:
        keywords = json.loads(row["keywords_json"] or "[]")
    except (TypeError, ValueError):
        keywords = []
    body = (f"💬 قاعدة التعليقات #{rule_id}\nالقناة: {row['account_name']}\n"
            f"الفيديو: {row['media_id']}\nالحالة: {'مفعّلة' if row['enabled'] else 'متوقفة'}\n"
            f"الكلمات: {', '.join(map(str, keywords)) or 'لا توجد'}\n"
            f"الرد العام: {(row['public_reply_text'] or 'غير مضبوط')[:400]}\n"
            f"الرد الخاص: {(row['private_reply_text'] or 'غير مضبوط')[:400]}\n"
            f"آخر خطأ: {(row['last_error'] or 'لا يوجد')[:200]}")
    _send(client, token, chat_id, body, {"inline_keyboard": [
        [{"text": "✏️ تعديل الرد العام", "callback_data": f"bs:comment:edit:{rule_id}:public"}],
        [{"text": "✏️ تعديل الرد الخاص", "callback_data": f"bs:comment:edit:{rule_id}:private"}],
        [{"text": "📥 عرض التعليقات", "callback_data": f"bs:comment:fetch:{rule_id}"}],
        [{"text": "📜 سجل الردود", "callback_data": f"bs:comment:logs:{rule_id}"}],
        [{"text": f"{'⏸ إيقاف' if row['enabled'] else '▶️ تشغيل'} الأتمتة", "callback_data": f"bs:comment:{rule_id}"}],
        [{"text": "↩ إدارة التعليقات", "callback_data": "bs:comments"}],
    ]})


def _comment_live_list(conn, client, graph_client_for, token, chat_id, rule_id):
    row = conn.execute("""SELECT r.media_id,c.id,c.platform,c.remote_account_id,c.linked_page_id,
               c.access_token,c.instagram_auth_method,c.zernio_account_id
        FROM instagram_comment_rules r JOIN channels c ON c.id=r.channel_id
        WHERE r.id=? AND c.platform='instagram' AND c.is_active=1
          AND (
            (c.zernio_account_id IS NOT NULL AND TRIM(c.zernio_account_id)<>'')
            OR (c.remote_account_id IS NOT NULL AND c.access_token IS NOT NULL)
          )""", (rule_id,)).fetchone()
    if row is None:
        _send(client, token, chat_id, "القاعدة غير متاحة أو القناة غير متصلة.", _menu())
        return
    graph = None
    try:
        if row["zernio_account_id"]:
            from .zernio_client import ZernioClient
            comments = ZernioClient().list_post_comments(
                str(row["media_id"]), account_id=row["zernio_account_id"], limit=25
            )
            lines = [f"💬 أحدث التعليقات للقاعدة #{rule_id} عبر Zernio:"]
            for item in comments[:12]:
                author = item.get("author") or {}
                username = (
                    author.get("username") if isinstance(author, dict) else author
                ) or item.get("username") or item.get("authorName") or "مستخدم"
                lines.append(f"\n{username}: {str(item.get('text') or '[تعليق بلا نص]')[:500]}")
            if not comments:
                lines.append("لا توجد تعليقات ظاهرة.")
            _send(client, token, chat_id, "\n".join(lines)[:3900], {"inline_keyboard":[[
                {"text":"↩ تفاصيل القاعدة","callback_data":f"bs:comment:detail:{rule_id}"}
            ]]})
            return

        if graph_client_for is None:
            raise RuntimeError("Meta client is not configured for this legacy channel.")
        graph = graph_client_for(row)
        comments, _ = graph.get_media_comments(str(row["media_id"]), row["access_token"], limit=25)
        lines = [f"💬 أحدث التعليقات للقاعدة #{rule_id}:"]
        for item in comments[:12]:
            author = (item.get("from") or {}).get("username") or item.get("username") or "مستخدم"
            lines.append(f"\n{author}: {str(item.get('text') or '[تعليق بلا نص]')[:500]}")
        if not comments:
            lines.append("لا توجد تعليقات ظاهرة.")
        _send(client, token, chat_id, "\n".join(lines)[:3900], {"inline_keyboard":[[
            {"text":"↩ تفاصيل القاعدة","callback_data":f"bs:comment:detail:{rule_id}"}
        ]]})
    except Exception as exc:
        from .redact import redact
        hint = (
            "تعذر الاتصال بخدمة Zernio؛ تحقق من ZERNIO_API_KEY وصلاحية التعليقات."
            if row["zernio_account_id"] else
            _comment_permission_hint(row, graph) if graph is not None else "تعذر تهيئة عميل Meta."
        )
        _send(client, token, chat_id, f"تعذر جلب التعليقات: {redact(str(exc))[:400]}\n{hint}", _menu())


def _dm_list(conn, client, graph_client_for, token, chat_id):
    channels = _connected_ig_channels(conn)
    if not channels:
        _send(client, token, chat_id, "لا توجد قناة Instagram متصلة ونشطة.", _menu())
        return
    if graph_client_for is None and not any(ch["zernio_account_id"] for ch in channels):
        _send(client, token, chat_id, "خدمة الرسائل غير متاحة حالياً.", _menu())
        return
    lines = ["✉️ محادثات Instagram الخاصة:"]
    keyboard = []
    with conn:
        conn.execute("DELETE FROM telegram_dm_conversation_refs WHERE chat_id=?", (chat_id,))
    for channel in channels:
        graph = None
        try:
            if channel["zernio_account_id"]:
                from .zernio_client import ZernioClient
                rows = ZernioClient().list_inbox_conversations(
                    account_id=channel["zernio_account_id"], limit=50
                )
                lines.append(f"\n{channel['account_name']}: {len(rows)} محادثة عبر Zernio")
                for conv in rows[:8]:
                    cid = str(conv.get("id") or "")
                    if not cid or len(cid) > 512:
                        continue
                    username = str(conv.get("participantName") or conv.get("participantId") or "مستخدم")
                    with conn:
                        ref = conn.execute(
                            """INSERT INTO telegram_dm_conversation_refs
                               (chat_id,channel_id,conversation_id,created_at) VALUES(?,?,?,?)""",
                            (chat_id, int(channel["id"]), cid, datetime.now(timezone.utc).isoformat()),
                        )
                        ref_id = int(ref.lastrowid)
                    lines.append(f"• {username} · {conv.get('updatedTime') or ''} · غير المقروء: {conv.get('unreadCount') or 0}")
                    keyboard.append([{"text": f"فتح {username[:18]} / قناة {channel['id']}",
                                      "callback_data": f"bs:dm:view:{ref_id}"}])
                continue

            if graph_client_for is None:
                raise RuntimeError("Meta client is not configured for this legacy channel.")
            graph = graph_client_for(channel)
            messaging_id = str(channel["remote_account_id"]) if _uses_instagram_login(channel, graph) else str(channel["linked_page_id"] or "")
            if not messaging_id:
                lines.append(f"تعذر قراءة {channel['account_name']}: يلزم ربط صفحة Facebook لهذه القناة.")
                continue
            rows = graph.get_instagram_conversations(messaging_id, channel["access_token"], limit=15)
            lines.append(f"\n{channel['account_name']}: {len(rows)} محادثة")
            own_ids = {str(channel["remote_account_id"] or ""), str(channel["linked_page_id"] or "")}
            for conv in rows[:8]:
                cid = str(conv.get("id") or "")
                if not cid or len(cid) > 512:
                    continue
                people = (conv.get("participants") or {}).get("data") or []
                peer = next((p for p in people if str(p.get("id") or "") not in own_ids), {})
                username = peer.get("username") or "مستخدم"
                with conn:
                    ref = conn.execute(
                        """INSERT INTO telegram_dm_conversation_refs
                           (chat_id,channel_id,conversation_id,created_at) VALUES(?,?,?,?)""",
                        (chat_id, int(channel["id"]), cid, datetime.now(timezone.utc).isoformat()),
                    )
                    ref_id = int(ref.lastrowid)
                lines.append(f"• {username} · {conv.get('updated_time') or ''}")
                keyboard.append([{"text": f"فتح {username[:18]} / قناة {channel['id']}",
                                  "callback_data": f"bs:dm:view:{ref_id}"}])
        except Exception as exc:
            from .redact import redact
            hint = (
                "تعذر الاتصال بخدمة Zernio؛ تحقق من مفتاح ZERNIO_API_KEY وصلاحية الرسائل."
                if channel["zernio_account_id"] else
                _messaging_permission_hint(channel, graph) if graph is not None else "تعذر تهيئة عميل Meta."
            )
            lines.append(f"تعذر جلب الرسائل: {redact(str(exc))[:220]}\n{hint}")
    keyboard.append([{"text": "↩ القائمة", "callback_data": "bs:menu"}])
    _send(client, token, chat_id, "\n".join(lines)[:3900], {"inline_keyboard": keyboard})


def _dm_view(conn, client, graph_client_for, token, chat_id, channel_id, conversation_id):
    channel = conn.execute("""SELECT id,platform,account_name,remote_account_id,linked_page_id,
        access_token,instagram_auth_method,zernio_account_id,zernio_profile_id FROM channels
        WHERE id=? AND platform='instagram' AND is_active=1
          AND (
            (zernio_account_id IS NOT NULL AND TRIM(zernio_account_id)<>'')
            OR (remote_account_id IS NOT NULL AND access_token IS NOT NULL)
          )""", (channel_id,)).fetchone()
    if channel is None:
        _send(client, token, chat_id, "القناة غير متصلة.", _menu())
        return
    graph = None
    try:
        if channel["zernio_account_id"]:
            from .zernio_client import ZernioClient
            zernio = ZernioClient()
            conversations = zernio.list_inbox_conversations(
                account_id=channel["zernio_account_id"], limit=100
            )
            conversation = next(
                (item for item in conversations if str(item.get("id") or "") == str(conversation_id)),
                {},
            )
            messages = zernio.list_inbox_messages(
                conversation_id, account_id=channel["zernio_account_id"], limit=50, sort_order="desc"
            )
            recipient = str(conversation.get("participantId") or "")
            if not recipient:
                incoming = next((m for m in messages if m.get("direction") == "incoming"), {})
                recipient = str(incoming.get("senderId") or "unknown")
            peer_name = str(conversation.get("participantName") or recipient or "العميل")
            lines = [f"✉️ {peer_name} · {channel['account_name']} · Zernio"]
            for msg in reversed(messages[:10]):
                who = "حسابك" if msg.get("direction") == "outgoing" else str(msg.get("senderName") or peer_name)
                lines.append(f"\n{who}: {str(msg.get('message') or '[مرفق أو رسالة بلا نص]')[:650]}")
            window_open, window_error = _zernio_dm_window(messages, datetime.now(timezone.utc))
            lines.append("\nنافذة الرد اليدوي: " + ("مفتوحة" if window_open else "مغلقة/غير مؤكدة"))
            with conn:
                conn.execute("""INSERT INTO telegram_dm_reply_sessions
                    (chat_id,channel_id,conversation_id,recipient_id,created_at) VALUES(?,?,?,?,?)
                    ON CONFLICT(chat_id) DO UPDATE SET channel_id=excluded.channel_id,
                    conversation_id=excluded.conversation_id,recipient_id=excluded.recipient_id,
                    created_at=excluded.created_at""",
                    (chat_id, channel_id, conversation_id, recipient, datetime.now(timezone.utc).isoformat()))
            _send(client, token, chat_id, "\n".join(lines)[:3900], {"inline_keyboard": [
                [{"text": "✍️ كتابة رد يدوي", "callback_data": "bs:dm:compose"}],
                [{"text": "↩ صندوق الرسائل", "callback_data": "bs:dms"}],
            ]})
            return

        if graph_client_for is None:
            raise RuntimeError("Meta client is not configured for this legacy channel.")
        graph = graph_client_for(channel)
        messaging_id = str(channel["remote_account_id"]) if _uses_instagram_login(channel, graph) else str(channel["linked_page_id"] or "")
        if not messaging_id:
            _send(client, token, chat_id, "يلزم ربط صفحة Facebook للقناة قبل قراءة الرسائل عبر Facebook Login.", _menu())
            return
        conversation, messages = graph.get_instagram_conversation_messages(conversation_id, channel["access_token"])
        people = (conversation.get("participants") or {}).get("data") or []
        own_ids = {str(channel["remote_account_id"] or ""), str(channel["linked_page_id"] or "")}
        peer = next((p for p in people if str(p.get("id") or "") not in own_ids), {})
        recipient = str(peer.get("id") or "")
        if not recipient:
            _send(client, token, chat_id, "تعذر تحديد مستلم المحادثة؛ لم يتم تجهيز إرسال.", _menu())
            return
        lines = [f"✉️ {peer.get('username') or recipient} · {channel['account_name']}"]
        for msg in messages[-10:]:
            sender = msg.get("from") or {}
            who = "حسابك" if str(sender.get("id") or "") in own_ids else (sender.get("username") or "العميل")
            lines.append(f"\n{who}: {str(msg.get('message') or '[مرفق أو رسالة بلا نص]')[:650]}")
        with conn:
            conn.execute("""INSERT INTO telegram_dm_reply_sessions
                (chat_id,channel_id,conversation_id,recipient_id,created_at) VALUES(?,?,?,?,?)
                ON CONFLICT(chat_id) DO UPDATE SET channel_id=excluded.channel_id,
                conversation_id=excluded.conversation_id,recipient_id=excluded.recipient_id,
                created_at=excluded.created_at""",
                (chat_id, channel_id, conversation_id, recipient, datetime.now(timezone.utc).isoformat()))
        _send(client, token, chat_id, "\n".join(lines)[:3900], {"inline_keyboard": [
            [{"text": "✍️ كتابة رد يدوي", "callback_data": "bs:dm:compose"}],
            [{"text": "↩ صندوق الرسائل", "callback_data": "bs:dms"}],
        ]})
    except Exception as exc:
        from .redact import redact
        hint = (
            "تعذر الاتصال بخدمة Zernio؛ تحقق من مفتاح ZERNIO_API_KEY وصلاحية الرسائل."
            if channel["zernio_account_id"] else
            _messaging_permission_hint(channel, graph) if graph is not None else "تعذر تهيئة عميل Meta."
        )
        _send(client, token, chat_id, f"تعذر فتح المحادثة: {redact(str(exc))[:350]}\n{hint}", _menu())



def process_control_update(conn, config, client, update: dict, graph_client_for=None) -> bool:
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
            with conn:
                conn.execute("DELETE FROM telegram_schedule_sessions WHERE chat_id=?", (chat_id,))
                conn.execute("DELETE FROM telegram_comment_edit_sessions WHERE chat_id=?", (chat_id,))
                conn.execute("DELETE FROM telegram_dm_reply_sessions WHERE chat_id=?", (chat_id,))
                conn.execute("DELETE FROM telegram_dm_conversation_refs WHERE chat_id=?", (chat_id,))
                conn.execute("UPDATE telegram_dm_outbox SET status='cancelled',updated_at=? WHERE chat_id=? AND status='pending'", (datetime.now(timezone.utc).isoformat(), chat_id))
                conn.execute(
                    """INSERT OR IGNORE INTO telegram_ingest_updates
                       (update_id, chat_id, status) VALUES (?, ?, 'ignored')""",
                    (int(update_id), chat_id),
                )
            _send(client, token, chat_id, "🕷 BLACK SPIDER\nاختر عملية:", _menu())
            return True
        if data == "bs:publishing_control":
            paused = conn.execute(
                "SELECT is_paused FROM publishing_control WHERE id=1"
            ).fetchone()["is_paused"]
            action = "resume" if paused else "pause"
            label = "▶️ استئناف النشر" if paused else "⏸ إيقاف النشر"
            state = "متوقف مؤقتاً" if paused else "نشط"
            with conn:
                conn.execute(
                    """INSERT OR IGNORE INTO telegram_ingest_updates
                       (update_id, chat_id, status) VALUES (?, ?, 'ignored')""",
                    (int(update_id), chat_id),
                )
            _send(client, token, chat_id,
                  f"⏯ التحكم بالنشر\nالحالة العامة: {state}\nهذا التحكم يشمل جميع القنوات. المنشورات ومواعيدها محفوظة.",
                  {"inline_keyboard": [
                      [{"text": label, "callback_data": f"bs:publishing:{action}"}],
                      [{"text": "↩ القائمة", "callback_data": "bs:menu"}],
                  ]})
            return True
        if data in {"bs:publishing:pause", "bs:publishing:resume"}:
            should_pause = 1 if data.endswith(":pause") else 0
            now = datetime.now(timezone.utc).isoformat()
            with conn:
                conn.execute(
                    """UPDATE publishing_control
                          SET is_paused=?, updated_at=?, updated_by_chat_id=?
                        WHERE id=1""",
                    (should_pause, now, chat_id),
                )
                conn.execute(
                    """INSERT OR IGNORE INTO telegram_ingest_updates
                       (update_id, chat_id, status) VALUES (?, ?, 'ignored')""",
                    (int(update_id), chat_id),
                )
            from .operation_log import record_control_operation
            record_control_operation(
                conn, int(update_id),
                "إيقاف النشر" if should_pause else "استئناف النشر",
                "تم تحديث حالة التحكم العام بالنشر من Telegram.",
                now=datetime.fromisoformat(now),
            )
            state = "متوقف مؤقتاً" if should_pause else "نشط"
            label = "▶️ استئناف النشر" if should_pause else "⏸ إيقاف النشر"
            action = "resume" if should_pause else "pause"
            message = (
                "تم إيقاف بدء عمليات النشر الجديدة على جميع القنوات. "
                "العملية التي بدأت بالفعل ستكمل بأمان، وبقية المنشورات ومواعيدها محفوظة."
                if should_pause else
                "تم استئناف النشر على جميع القنوات. سيعالج العامل المنشورات المستحقة والمتأخرة تلقائياً حسب ترتيب الطابور وإعدادات إعادة المحاولة."
            )
            _send(client, token, chat_id,
                  f"{message}\nالحالة الحالية: {state}",
                  {"inline_keyboard": [
                      [{"text": label, "callback_data": f"bs:publishing:{action}"}],
                      [{"text": "↩ القائمة", "callback_data": "bs:menu"}],
                  ]})
            return True
        if data == "bs:operations" or data.startswith("bs:operations:page:"):
            page = 0
            if data != "bs:operations":
                raw_page = data.removeprefix("bs:operations:page:")
                if not raw_page.isdigit() or len(raw_page) > 5:
                    _send(client, token, chat_id, "رقم صفحة غير صالح.", _menu())
                    return True
                page = int(raw_page)
            page_size = 8
            total = conn.execute("SELECT COUNT(*) FROM telegram_operation_log").fetchone()[0]
            page_count = max(1, (total + page_size - 1) // page_size)
            page = min(page, page_count - 1)
            rows = conn.execute(
                """SELECT id,event_type,status,publication_id,channel_name,caption,detail,created_at
                     FROM telegram_operation_log
                    ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?""",
                (page_size, page * page_size),
            ).fetchall()
            if not rows:
                body = "📋 سجل العمليات فارغ حالياً. ستظهر هنا نتائج النشر والمحاولات والأخطاء."
            else:
                labels = {
                    "success": "✅ نجاح", "retrying": "⚠️ إعادة محاولة",
                    "failed": "❌ فشل", "dry_run": "🧪 محاكاة", "info": "ℹ️ معلومات",
                }
                lines = [f"📋 سجل العمليات · صفحة {page + 1}/{page_count}"]
                log_timezone = getattr(config, "default_timezone", None) or "UTC"
                for row in rows:
                    stamp = _local_stamp(row["created_at"], log_timezone)
                    lines.append(
                        f"\n#{row['id']} · {labels.get(row['status'], row['status'])} · {stamp} ({log_timezone})\n"
                        f"{row['event_type']} · منشور #{row['publication_id'] or '—'} · "
                        f"{row['channel_name'] or 'قناة غير محددة'}\n"
                        f"{(row['caption'] or 'بدون وصف')[:100]}\n"
                        f"{(row['detail'] or 'لا توجد تفاصيل')[:260]}"
                    )
                body = "\n".join(lines)[:3900]
            navigation = []
            if page > 0:
                navigation.append({"text": "⬅ الأحدث", "callback_data": f"bs:operations:page:{page - 1}"})
            if page + 1 < page_count:
                navigation.append({"text": "الأقدم ➡", "callback_data": f"bs:operations:page:{page + 1}"})
            keyboard = []
            if navigation:
                keyboard.append(navigation)
            keyboard.append([{"text": "↩ القائمة", "callback_data": "bs:menu"}])
            _send(client, token, chat_id, body, {"inline_keyboard": keyboard})
            return True

        if data == "bs:account:add":
            base = str(getattr(config, "telegram_dashboard_url", "") or "").strip().rstrip("/")
            if not base.startswith("https://"):
                _send(client, token, chat_id, "تعذر بدء ربط Instagram. تحقق من إعداد رابط HTTPS للوحة التحكم.", _menu())
                return True
            ticket = create_link_ticket(conn, chat_id=chat_id, user_id=sender_id, platform="instagram")
            url = base + "/api/channels/instagram/zernio/telegram?" + urlencode({"ticket": ticket.token})
            _send(client, token, chat_id, "افتح الرابط لإضافة حساب Instagram خلال 10 دقائق. سجّل الدخول في صفحة Instagram الرسمية؛ لا ترسل كلمة المرور أو رمز التحقق للبوت.", {"inline_keyboard": [[{"text": "➕ تسجيل دخول Instagram", "url": url}]]})
            return True
        if data.startswith("bs:account:select:"):
            raw_id = data.removeprefix("bs:account:select:")
            if not raw_id.isdigit():
                _send(client, token, chat_id, "رقم الحساب غير صالح.", _menu())
                return True
            account = conn.execute(
                """SELECT id, instagram_user_id, username, status
                     FROM instagram_accounts
                    WHERE id=? AND telegram_chat_id=? AND telegram_user_id=?""",
                (int(raw_id), chat_id, sender_id),
            ).fetchone()
            if account is None or account["status"] != "connected":
                _send(client, token, chat_id, "الحساب غير موجود أو غير متصل. حدّث قائمة الحسابات.", _menu())
                return True
            conn.execute(
                """INSERT INTO telegram_account_preferences
                   (telegram_chat_id, telegram_user_id, selected_instagram_account_id, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(telegram_chat_id, telegram_user_id) DO UPDATE SET
                     selected_instagram_account_id=excluded.selected_instagram_account_id,
                     updated_at=excluded.updated_at""",
                (chat_id, sender_id, int(account["id"]), datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            _send(client, token, chat_id,
                  f"✅ تم اختيار حساب Instagram @{account['username']} للإدارة.\n"
                  f"معرّف الحساب: {account['instagram_user_id']}",
                  {"inline_keyboard": [
                      [{"text": "👤 الحسابات المرتبطة", "callback_data": "bs:accounts"}],
                      [{"text": "↩ القائمة", "callback_data": "bs:menu"}],
                  ]})
            return True
        if data == "bs:accounts":
            rows = conn.execute(
                """SELECT id, instagram_user_id, username, status, token_expires_at,
                          last_verified_at
                     FROM instagram_accounts
                    WHERE telegram_chat_id=? AND telegram_user_id=?
                    ORDER BY created_at DESC, id DESC""",
                (chat_id, sender_id),
            ).fetchall()
            selected = conn.execute(
                """SELECT selected_instagram_account_id FROM telegram_account_preferences
                    WHERE telegram_chat_id=? AND telegram_user_id=?""",
                (chat_id, sender_id),
            ).fetchone()
            selected_id = int(selected["selected_instagram_account_id"]) if selected and selected["selected_instagram_account_id"] else None
            lines = ["👤 حسابات Instagram المرتبطة"]
            keyboard = []
            if not rows:
                lines.append("\nلا توجد حسابات مرتبطة حتى الآن.")
            else:
                for row in rows:
                    state = {
                        "connected": "✅ متصل",
                        "expired": "⌛ انتهت صلاحية الاتصال",
                        "revoked": "⛔ تم إلغاء الصلاحية",
                        "error": "⚠️ يحتاج إلى إعادة ربط",
                    }.get(row["status"], "⚠️ حالة غير معروفة")
                    selected_label = " ⭐ الحساب المختار" if int(row["id"]) == selected_id else ""
                    lines.append(
                        f"\n@{row['username']}{selected_label}\n"
                        f"المعرّف: {row['instagram_user_id']}\n"
                        f"الحالة: {state}"
                    )
                    if row["status"] == "connected":
                        keyboard.append([{
                            "text": ("✓ " if int(row["id"]) == selected_id else "اختيار ")
                                    + "@" + str(row["username"])[:24],
                            "callback_data": f"bs:account:select:{row['id']}",
                        }])
            keyboard.extend([
                [{"text": "➕ إضافة حساب Instagram", "callback_data": "bs:account:add"}],
                [{"text": "🔄 تحديث", "callback_data": "bs:accounts"}],
                [{"text": "↩ القائمة", "callback_data": "bs:menu"}],
            ])
            _send(client, token, chat_id, "\n".join(lines)[:3300], {"inline_keyboard": keyboard})
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
                  f"📊 حالة النظام\nآخر نبضة Worker: {stamp}\nقنوات Instagram المتصلة والنشطة: {channels}\nحالة النشر العامة: {'متوقف مؤقتاً' if conn.execute('SELECT is_paused FROM publishing_control WHERE id=1').fetchone()['is_paused'] else 'نشط'}\nمنشورات مجدولة: {queued}\nمنشورات فاشلة: {failed}",
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
                    caption = (row["caption"] or "بدون وصف").replace("\n", " ")[:70]
                    lines.append(f"#{row['id']} · {row['status']} · {row['scheduled_at']}\n{caption}")
                body = "\n".join(lines)
            _send(client, token, chat_id, body, _menu())
            return True

        if data == "bs:schedule":
            with conn:
                conn.execute("DELETE FROM telegram_schedule_sessions WHERE chat_id=?", (chat_id,))
                conn.execute(
                    """INSERT OR IGNORE INTO telegram_ingest_updates
                       (update_id, chat_id, status) VALUES (?, ?, 'ignored')""",
                    (int(update_id), chat_id),
                )
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
                from .operation_log import record_control_operation
                record_control_operation(
                    conn, int(update_id), "إلغاء جدولة",
                    "أُلغي الموعد وحُفظ المنشور وملفاته دون حذف.",
                    publication_id=publication_id, now=datetime.fromisoformat(now),
                )
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
                caption = (row["caption"] or "بدون وصف").replace("\n", " ")[:60]
                lines.append(f"#{row['id']} · {row['scheduled_at']}\n{caption}")
            _send(client, token, chat_id, "\n".join(lines), keyboard)
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
                if updated.rowcount == 1 and update_id is not None:
                    conn.execute(
                        """INSERT OR IGNORE INTO telegram_ingest_updates
                           (update_id, chat_id, status) VALUES (?, ?, 'ignored')""",
                        (int(update_id), chat_id),
                    )
            if updated.rowcount != 1:
                _send(client, token, chat_id,
                      "لم يتم تقديم المنشور: لم يعد مجدولاً أو أن قناة Instagram غير متصلة.", _menu())
                return True
            from .operation_log import record_control_operation
            record_control_operation(
                conn, int(update_id), "تقديم موعد للنشر",
                "تم تقديم الموعد إلى الآن؛ هذا لا يعني أن Instagram أكد النشر.",
                publication_id=publication_id, now=datetime.fromisoformat(now),
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
                      AND (
                        (c.zernio_account_id IS NOT NULL AND TRIM(c.zernio_account_id)<>'')
                        OR (c.remote_account_id IS NOT NULL AND TRIM(c.remote_account_id)<>''
                            AND c.access_token IS NOT NULL AND TRIM(c.access_token)<>'')
                      )
                    ORDER BY r.id DESC LIMIT 8"""
            ).fetchall()
            if not rows:
                _send(client, token, chat_id, "لا توجد قواعد تعليقات مرتبطة بقنوات Instagram نشطة.", _menu())
                return True
            keyboard = [[{"text": f"{'🟢' if row['enabled'] else '⚪'} قاعدة #{row['id']} · {row['account_name']}",
                          "callback_data": f"bs:comment:detail:{row['id']}"}] for row in rows]
            keyboard.append([{"text": "↩ القائمة", "callback_data": "bs:menu"}])
            _send(client, token, chat_id, "💬 إدارة التعليقات\nاختر قاعدة لعرض الردود والتحكم بها:", {"inline_keyboard": keyboard})
            return True
        if data == "bs:dms":
            _dm_list(conn, client, graph_client_for, token, chat_id)
            return True
        if data.startswith("bs:dm:view:"):
            parts = data.split(":")
            if len(parts) == 4 and parts[3].isdigit() and len(parts[3]) <= 9:
                ref = conn.execute(
                    """SELECT id,channel_id,conversation_id,created_at
                         FROM telegram_dm_conversation_refs
                        WHERE id=? AND chat_id=?""",
                    (int(parts[3]), chat_id),
                ).fetchone()
                if ref is None or _session_expired(ref["created_at"]):
                    with conn:
                        conn.execute("DELETE FROM telegram_dm_conversation_refs WHERE chat_id=?", (chat_id,))
                    _send(client, token, chat_id, "انتهت صلاحية قائمة المحادثات. افتح صندوق الرسائل مجدداً.", _menu())
                else:
                    _dm_view(conn, client, graph_client_for, token, chat_id,
                             int(ref["channel_id"]), str(ref["conversation_id"]))
            elif len(parts) == 5 and parts[3].isdigit() and parts[4].isdigit():
                # Backward compatibility for numeric callback buttons created before this
                # release; new lists use short chat-bound references for opaque Meta IDs.
                _dm_view(conn, client, graph_client_for, token, chat_id, int(parts[3]), parts[4])
            else:
                _send(client, token, chat_id, "طلب فتح محادثة غير صالح.", _menu())
            return True
        if data == "bs:dm:compose":
            session = conn.execute("SELECT created_at FROM telegram_dm_reply_sessions WHERE chat_id=?", (chat_id,)).fetchone()
            if session and not _session_expired(session["created_at"]):
                _send(client, token, chat_id, "اكتب نص الرد الآن. لن يُرسل حتى تراجع المعاينة وتضغط «تأكيد وإرسال». أرسل /menu للإلغاء.")
            else:
                with conn:
                    conn.execute("DELETE FROM telegram_dm_reply_sessions WHERE chat_id=?", (chat_id,))
                _send(client, token, chat_id, "انتهت جلسة المحادثة. افتحها من صندوق الرسائل مجدداً.", _menu())
            return True
        if data.startswith("bs:dm:cancel:"):
            raw_id = data.removeprefix("bs:dm:cancel:")
            if not raw_id.isdigit() or len(raw_id) > 9:
                _send(client, token, chat_id, "معرّف الإلغاء غير صالح.", _menu())
                return True
            with conn:
                canceled = conn.execute(
                    "UPDATE telegram_dm_outbox SET status='cancelled',updated_at=? "
                    "WHERE id=? AND chat_id=? AND status='pending'",
                    (datetime.now(timezone.utc).isoformat(), int(raw_id), chat_id),
                )
                if update_id is not None:
                    conn.execute(
                        """INSERT OR IGNORE INTO telegram_ingest_updates
                           (update_id,chat_id,status) VALUES(?,?,'ignored')""",
                        (int(update_id), chat_id),
                    )
            if canceled.rowcount == 1:
                _send(client, token, chat_id, "تم إلغاء الرد. لن يمكن إرساله من زر التأكيد القديم.", _menu())
            else:
                _send(client, token, chat_id, "هذا الرد لم يعد معلّقاً؛ لم يتم إرسال أي طلب جديد.", _menu())
            return True
        if data.startswith("bs:dm:send:"):
            raw_id = data.removeprefix("bs:dm:send:")
            if not raw_id.isdigit() or len(raw_id) > 9:
                _send(client, token, chat_id, "معرّف الإرسال غير صالح.", _menu())
                return True
            outbox_id = int(raw_id)
            now_dt = datetime.now(timezone.utc)
            now = now_dt.isoformat()
            cutoff = (now_dt - timedelta(minutes=20)).isoformat()
            with conn:
                claim = conn.execute("""UPDATE telegram_dm_outbox SET status='sending',updated_at=?
                    WHERE id=? AND chat_id=? AND status='pending' AND created_at>=?""",
                    (now, outbox_id, chat_id, cutoff))
                if claim.rowcount != 1:
                    conn.execute("""UPDATE telegram_dm_outbox SET status='cancelled',updated_at=?
                        WHERE id=? AND chat_id=? AND status='pending' AND created_at<?""",
                        (now, outbox_id, chat_id, cutoff))
                row = conn.execute("SELECT * FROM telegram_dm_outbox WHERE id=? AND chat_id=?",
                                   (outbox_id, chat_id)).fetchone()
                if update_id is not None:
                    conn.execute(
                        """INSERT OR IGNORE INTO telegram_ingest_updates
                           (update_id,chat_id,status) VALUES(?,?,'ignored')""",
                        (int(update_id), chat_id),
                    )
            if claim.rowcount != 1 or row is None:
                _send(client, token, chat_id, "انتهت صلاحية المعاينة أو سبق تأكيدها/إلغاؤها؛ افتح المحادثة وأنشئ معاينة جديدة.", _menu())
                return True
            try:
                provider_label = "Meta"
                channel = conn.execute("""SELECT id,platform,remote_account_id,linked_page_id,access_token,
                    instagram_auth_method,zernio_account_id,zernio_profile_id FROM channels
                    WHERE id=? AND platform='instagram' AND is_active=1""", (row["channel_id"],)).fetchone()
                if channel is None:
                    raise RuntimeError("قناة Instagram غير متصلة.")
                if channel["zernio_account_id"]:
                    from .zernio_client import ZernioClient
                    provider_label = "Zernio"
                    zernio = ZernioClient()
                    latest_messages = zernio.list_inbox_messages(
                        row["conversation_id"], account_id=channel["zernio_account_id"],
                        limit=100, sort_order="desc",
                    )
                    window_open, window_error = _zernio_dm_window(latest_messages, now_dt)
                    if not window_open:
                        with conn:
                            conn.execute(
                                "UPDATE telegram_dm_outbox SET status='cancelled',last_error=?,updated_at=? WHERE id=?",
                                (window_error, datetime.now(timezone.utc).isoformat(), outbox_id),
                            )
                        from .operation_log import mark_operation_notified, record_manual_dm_outcome
                        event = record_manual_dm_outcome(
                            conn, outbox_id, status="info", detail=window_error)
                        _send(client, token, chat_id, f"⛔ {window_error}", _menu())
                        if event:
                            mark_operation_notified(conn, int(event["id"]))
                        return True
                    result = zernio.send_inbox_message(
                        row["conversation_id"],
                        account_id=channel["zernio_account_id"],
                        message=row["message_text"],
                        idempotency_key=f"black-spider-human-dm-{outbox_id}",
                    )
                    sent_data = result.get("data") or {}
                    sent_message = result.get("message") or sent_data.get("message") or {}
                    meta_message_id = str(
                        sent_message.get("id") or sent_message.get("messageId")
                        or sent_data.get("messageId") or result.get("messageId") or ""
                    ) if isinstance(sent_message, dict) else str(sent_data.get("messageId") or result.get("messageId") or "")
                    if not meta_message_id:
                        raise RuntimeError("Zernio accepted the request but returned no message ID; delivery cannot be confirmed safely.")
                else:
                    if graph_client_for is None:
                        raise RuntimeError("عميل Meta غير متاح لهذه القناة.")
                    graph = graph_client_for(channel)
                    messaging_id = str(channel["remote_account_id"]) if _uses_instagram_login(channel, graph) else str(channel["linked_page_id"] or "")
                    if not messaging_id:
                        raise RuntimeError("يلزم ربط صفحة Facebook لإرسال الرسائل عبر Facebook Login.")
                    # Re-fetch at confirmation time: the inbox preview may have been opened
                    # while the 24-hour window was valid and confirmed after it closed.
                    _, latest_messages = graph.get_instagram_conversation_messages(
                        row["conversation_id"], channel["access_token"])
                    window_open, window_error = _instagram_dm_window(
                        latest_messages, row["recipient_id"], now_dt)
                    if not window_open:
                        with conn:
                            conn.execute(
                                "UPDATE telegram_dm_outbox SET status='cancelled',last_error=?,updated_at=? WHERE id=?",
                                (window_error, datetime.now(timezone.utc).isoformat(), outbox_id),
                            )
                        from .operation_log import mark_operation_notified, record_manual_dm_outcome
                        event = record_manual_dm_outcome(
                            conn, outbox_id, status="info", detail=window_error)
                        _send(client, token, chat_id, f"⛔ {window_error}", _menu())
                        if event:
                            mark_operation_notified(conn, int(event["id"]))
                        return True
                    result = graph.send_instagram_direct_message(
                        messaging_id, row["recipient_id"], row["message_text"], channel["access_token"])
                    meta_message_id = str(result.get("message_id") or result.get("id") or "")
                    if not meta_message_id:
                        raise RuntimeError("Meta returned no message ID; delivery cannot be confirmed safely.")
            except Exception as exc:
                from .redact import redact
                safe_error = redact(str(exc))[:500]
                with conn:
                    conn.execute("UPDATE telegram_dm_outbox SET status='unknown',last_error=?,updated_at=? WHERE id=?",
                                 (safe_error, datetime.now(timezone.utc).isoformat(), outbox_id))
                from .operation_log import mark_operation_notified, record_manual_dm_outcome
                event = record_manual_dm_outcome(
                    conn, outbox_id, status="info",
                    detail=f"نتيجة الإرسال غير مؤكدة؛ لم تتم إعادة المحاولة تلقائياً. {safe_error}",
                )
                _send(client, token, chat_id, "⚠️ نتيجة الإرسال غير مؤكدة. لن نعيد المحاولة تلقائياً لتجنب تكرار الرسالة.\n" + safe_error[:300], _menu())
                if event:
                    mark_operation_notified(conn, int(event["id"]))
                return True

            # Meta confirmed delivery. Keep this persistence path separate from the
            # Telegram acknowledgement: a failed Telegram send must never downgrade a
            # confirmed Meta delivery to an unknown outcome.
            with conn:
                conn.execute("UPDATE telegram_dm_outbox SET status='sent',meta_message_id=?,updated_at=? WHERE id=?",
                             (meta_message_id, datetime.now(timezone.utc).isoformat(), outbox_id))
            from .operation_log import mark_operation_notified, record_manual_dm_outcome
            event = record_manual_dm_outcome(
                conn, outbox_id, status="success",
                detail=f"أكدت {provider_label} الإرسال. معرّف الرسالة: {meta_message_id}",
            )
            _send(client, token, chat_id, f"✅ أكدت {provider_label} إرسال الرد بنجاح. رقم الرسالة: {meta_message_id}", _menu())
            if event:
                mark_operation_notified(conn, int(event["id"]))
            return True
        if data.startswith("bs:comment:fetch:"):
            raw_id = data.removeprefix("bs:comment:fetch:")
            if raw_id.isdigit() and len(raw_id) <= 9:
                _comment_live_list(conn, client, graph_client_for, token, chat_id, int(raw_id))
            else:
                _send(client, token, chat_id, "رقم القاعدة غير صالح.", _menu())
            return True
        if data.startswith("bs:comment:detail:"):
            raw_id = data.removeprefix("bs:comment:detail:")
            if raw_id.isdigit() and len(raw_id) <= 9:
                _comment_rule_detail(conn, client, token, chat_id, int(raw_id))
            else:
                _send(client, token, chat_id, "رقم القاعدة غير صالح.", _menu())
            return True
        if data.startswith("bs:comment:edit:"):
            parts = data.split(":")
            if len(parts) != 5 or not parts[3].isdigit() or parts[4] not in {"public", "private"}:
                _send(client, token, chat_id, "طلب التعديل غير صالح.", _menu())
            else:
                field = "public_reply_text" if parts[4] == "public" else "private_reply_text"
                valid = conn.execute("""SELECT 1 FROM instagram_comment_rules r JOIN channels c ON c.id=r.channel_id
                    WHERE r.id=? AND c.platform='instagram' AND c.is_active=1
                    AND (
                      (c.zernio_account_id IS NOT NULL AND TRIM(c.zernio_account_id)<>'')
                      OR (c.remote_account_id IS NOT NULL AND c.access_token IS NOT NULL)
                    )""", (int(parts[3]),)).fetchone()
                if not valid:
                    _send(client, token, chat_id, "القاعدة غير موجودة أو القناة غير متصلة.", _menu())
                else:
                    with conn:
                        conn.execute("""INSERT INTO telegram_comment_edit_sessions(chat_id,rule_id,field,created_at)
                            VALUES(?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET rule_id=excluded.rule_id,
                            field=excluded.field,created_at=excluded.created_at""",
                            (chat_id, int(parts[3]), field, datetime.now(timezone.utc).isoformat()))
                    _send(client, token, chat_id, "أرسل نص الرد الجديد الآن. لن يتم إرسال رد أثناء التعديل. /menu للإلغاء.")
            return True
        if data.startswith("bs:comment:logs:"):
            raw_id = data.removeprefix("bs:comment:logs:")
            if not raw_id.isdigit():
                _send(client, token, chat_id, "رقم القاعدة غير صالح.", _menu())
            else:
                rule_id = int(raw_id)
                rule = conn.execute("""SELECT r.zernio_automation_id,c.zernio_account_id
                    FROM instagram_comment_rules r JOIN channels c ON c.id=r.channel_id
                    WHERE r.id=? AND c.platform='instagram' AND c.is_active=1""",
                    (rule_id,)).fetchone()
                if rule and rule["zernio_account_id"]:
                    lines = [f"📜 سجل أتمتة Zernio للقاعدة #{rule_id}:"]
                    if not rule["zernio_automation_id"]:
                        lines.append("لم تتم مزامنة القاعدة مع Zernio بعد. المزامنة تعمل عندما يُسمح للعامل بتنفيذ الأتمتة.")
                    else:
                        try:
                            from .zernio_client import ZernioClient
                            result = ZernioClient().list_comment_automation_logs(
                                rule["zernio_automation_id"], limit=8, skip=0
                            )
                            labels = {
                                "sent": "أُرسل الرد الخاص",
                                "failed": "فشل الرد الخاص",
                                "skipped": "تم تخطي الرد",
                                "pending": "بانتظار الإرسال",
                                "gated": "بانتظار تأكيد المتابع",
                            }
                            for item in result["logs"]:
                                lines.append(
                                    f"\n{item.get('createdAt') or ''} · {labels.get(item.get('status'), item.get('status') or 'غير معروف')}"
                                    f"\n{item.get('commenterName') or item.get('commenterUsername') or 'مستخدم'}: "
                                    f"{str(item.get('commentText') or '')[:140]}"
                                )
                                if item.get("error"):
                                    lines.append(f"السبب: {str(item['error'])[:180]}")
                                if item.get("commentReplyStatus") in ("failed", "sent"):
                                    lines.append(f"الرد العام: {item['commentReplyStatus']}")
                            misses = result.get("misses") or {}
                            if misses.get("total"):
                                lines.append(
                                    f"تعليقات لم تطابق الكلمات: {misses['total']} خلال آخر {misses.get('retentionDays') or '?'} أيام"
                                )
                            if not result["logs"]:
                                lines.append("لا توجد نتائج ردود مسجلة حتى الآن.")
                        except Exception as exc:
                            from .redact import redact
                            lines.append(f"تعذر جلب سجل Zernio: {redact(str(exc))[:250]}")
                    _send(client, token, chat_id, "\n".join(lines)[:3900], {"inline_keyboard":[[
                        {"text":"↩ تفاصيل القاعدة","callback_data":f"bs:comment:detail:{rule_id}"}
                    ]]})
                else:
                    rows = conn.execute("""SELECT comment_id,comment_text,status,last_error,created_at
                        FROM instagram_comment_events WHERE rule_id=? ORDER BY created_at DESC LIMIT 10""",
                        (rule_id,)).fetchall()
                    lines = [f"📜 سجل آخر 10 تعليقات للقاعدة #{raw_id}:"]
                    for row in rows:
                        lines.append(f"\n{row['created_at']} · {row['status']} · #{row['comment_id']}\n"
                                     f"{(row['comment_text'] or '')[:160]}\n"
                                     f"{('خطأ: '+row['last_error'][:160]) if row['last_error'] else ''}")
                    _send(client, token, chat_id, "\n".join(lines)[:3900], {"inline_keyboard":[[
                        {"text":"↩ تفاصيل القاعدة","callback_data":f"bs:comment:detail:{raw_id}"}
                    ]]})
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
                      AND (
                        (c.zernio_account_id IS NOT NULL AND TRIM(c.zernio_account_id)<>'')
                        OR (c.remote_account_id IS NOT NULL AND TRIM(c.remote_account_id)<>''
                            AND c.access_token IS NOT NULL AND TRIM(c.access_token)<>'')
                      )""",
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
            from .operation_log import record_control_operation
            record_control_operation(
                conn, int(update_id), "تغيير أتمتة التعليقات",
                f"{'تم تشغيل' if new_value else 'تم إيقاف'} أتمتة التعليقات للقاعدة #{rule_id}.",
                rule_id=rule_id, now=datetime.now(timezone.utc),
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
        edit_session = conn.execute("SELECT rule_id, field, created_at FROM telegram_comment_edit_sessions WHERE chat_id=?", (chat_id,)).fetchone()
        dm_session = conn.execute("SELECT channel_id, conversation_id, recipient_id, created_at FROM telegram_dm_reply_sessions WHERE chat_id=?", (chat_id,)).fetchone()
        incoming_text = str(message.get("text") or "").strip()
        if incoming_text in {"/menu", "/start"}:
            with conn:
                conn.execute("DELETE FROM telegram_comment_edit_sessions WHERE chat_id=?", (chat_id,))
                conn.execute("DELETE FROM telegram_dm_reply_sessions WHERE chat_id=?", (chat_id,))
                conn.execute("DELETE FROM telegram_schedule_sessions WHERE chat_id=?", (chat_id,))
                conn.execute("UPDATE telegram_dm_outbox SET status='cancelled',updated_at=? WHERE chat_id=? AND status='pending'", (datetime.now(timezone.utc).isoformat(), chat_id))
            _send(client, token, chat_id, "تم إلغاء العملية الحالية وأي معاينات رد معلّقة.", _menu())
            return True
        if edit_session and _session_expired(edit_session["created_at"]):
            with conn:
                conn.execute("DELETE FROM telegram_comment_edit_sessions WHERE chat_id=?", (chat_id,))
            _send(client, token, chat_id, "انتهت مهلة تعديل الرد. ابدأ التعديل من تفاصيل القاعدة مجدداً.", _menu())
            return True
        if dm_session and _session_expired(dm_session["created_at"]):
            with conn:
                conn.execute("DELETE FROM telegram_dm_reply_sessions WHERE chat_id=?", (chat_id,))
            _send(client, token, chat_id, "انتهت مهلة كتابة الرد. افتح المحادثة مجدداً قبل كتابة رد جديد.", _menu())
            return True
        if edit_session:
            if not incoming_text or len(incoming_text) > 2200:
                _send(client, token, chat_id, "نص الرد يجب أن يكون بين حرف واحد و2200 حرف.")
                return True
            with conn:
                changed = conn.execute(f"UPDATE instagram_comment_rules SET {edit_session['field']}=?, updated_at=? WHERE id=?", (incoming_text, datetime.now(timezone.utc).isoformat(), edit_session["rule_id"]))
                conn.execute("DELETE FROM telegram_comment_edit_sessions WHERE chat_id=?", (chat_id,))
            if changed.rowcount:
                from .operation_log import record_control_operation
                record_control_operation(
                    conn, int(update_id), "تعديل رد التعليقات",
                    f"تم تحديث حقل الرد {edit_session['field']} للقاعدة #{edit_session['rule_id']}.",
                    rule_id=int(edit_session["rule_id"]), now=datetime.now(timezone.utc),
                )
            _send(client, token, chat_id, "تم حفظ نص الرد." if changed.rowcount else "لم يتم الحفظ؛ القاعدة غير موجودة.", _menu())
            return True
        if dm_session:
            if not incoming_text or len(incoming_text) > 1000:
                _send(client, token, chat_id, "اكتب رداً بين حرف واحد و1000 حرف. لم يتم الإرسال.")
                return True
            now = datetime.now(timezone.utc).isoformat()
            with conn:
                cur = conn.execute("INSERT INTO telegram_dm_outbox(chat_id,channel_id,conversation_id,recipient_id,message_text,status,created_at,updated_at) VALUES(?,?,?,?,?,'pending',?,?)", (chat_id, dm_session["channel_id"], dm_session["conversation_id"], dm_session["recipient_id"], incoming_text, now, now))
                outbox_id = cur.lastrowid
                conn.execute("DELETE FROM telegram_dm_reply_sessions WHERE chat_id=?", (chat_id,))
            _send(client, token, chat_id, "معاينة الرد:\n\n" + incoming_text + "\n\nلم تُرسل الرسالة بعد. راجع النص ثم أكد.", {"inline_keyboard":[[{"text":"✅ تأكيد وإرسال","callback_data":f"bs:dm:send:{outbox_id}"}],[{"text":"❌ إلغاء","callback_data":f"bs:dm:cancel:{outbox_id}"}]]})
            return True
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
                from .operation_log import record_control_operation
                record_control_operation(
                    conn, int(update_id), "تعديل موعد",
                    f"تم تعديل الموعد إلى {_local_stamp(scheduled_utc.isoformat(), session['timezone'])} ({session['timezone']}).",
                    publication_id=int(session["publication_id"]), now=now_dt,
                )
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
