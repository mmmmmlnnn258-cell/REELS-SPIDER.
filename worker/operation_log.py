"""Durable operation history and best-effort Telegram notifications for BLACK SPIDER."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .redact import redact

NOTIFICATION_RETRY_SECONDS = 60


def _allowed(raw: str) -> list[str]:
    return list(dict.fromkeys(part.strip() for part in (raw or "").split(",") if part.strip()))[:20]


def _telegram_client():
    from .telegram_api import TelegramClient
    return TelegramClient(timeout=15)


def _insert_event(conn, *, event_key: str, event_type: str, status: str,
                  publication_id=None, channel_name=None, caption=None, detail=None,
                  created_at=None):
    stamp = created_at or datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR IGNORE INTO telegram_operation_log
           (event_key,event_type,status,publication_id,channel_name,caption,detail,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (event_key, event_type, status, publication_id, channel_name,
         (caption or "")[:500], redact(str(detail or ""))[:1200], stamp),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM telegram_operation_log WHERE event_key=?", (event_key,)
    ).fetchone()


def _notify_once(conn, row, config, logger=None):
    if row is None or row["notified_at"] or not config.telegram_bot_token:
        return
    chats = _allowed(config.telegram_allowed_chat_ids)
    if not chats:
        return
    status = row["status"]
    if status == "dry_run":
        return
    icon = {"success": "✅", "retrying": "⚠️", "failed": "❌", "info": "ℹ️"}.get(status, "ℹ️")
    heading = (
        "تم تسليم الفيديو إلى صندوق النشر؛ لم يتم نشره على الحساب بعد"
        if row["event_type"] == "تسليم لصندوق النشر" else {
            "success": "تم النشر بنجاح",
            "retrying": "تعذرت محاولة النشر — ستتم إعادة المحاولة",
            "failed": "فشل النشر نهائياً",
            "info": "تحديث معلوماتي",
        }.get(status, "تحديث عملية")
    )
    caption = (row["caption"] or "بدون وصف").replace("\n", " ")[:180]
    message = (
        f"{icon} BLACK SPIDER\n{heading}\n"
        f"العملية: {row['event_type']} · #{row['publication_id'] or '—'}\n"
        f"القناة: {row['channel_name'] or 'غير محددة'}\n"
        f"المنشور: {caption}\n"
        f"التفاصيل: {(row['detail'] or 'لا توجد تفاصيل إضافية')[:900]}"
    )
    try:
        client = _telegram_client()
    except Exception as exc:
        if logger:
            logger.warning("BLACK SPIDER notification client unavailable for operation %s: %s",
                           row["id"], redact(str(exc))[:300])
        return
    for chat_id in chats:
        attempt = conn.execute(
            """SELECT last_attempt_at,notified_at FROM telegram_operation_notifications
                WHERE operation_id=? AND chat_id=?""",
            (row["id"], chat_id),
        ).fetchone()
        if attempt and attempt["notified_at"]:
            continue
        if attempt and attempt["last_attempt_at"]:
            try:
                last_attempt = datetime.fromisoformat(
                    str(attempt["last_attempt_at"]).replace("Z", "+00:00")
                )
                if last_attempt.tzinfo is None:
                    last_attempt = last_attempt.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - last_attempt).total_seconds() < NOTIFICATION_RETRY_SECONDS:
                    continue
            except (TypeError, ValueError):
                pass
        attempted_at = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                """INSERT INTO telegram_operation_notifications
                   (operation_id,chat_id,last_attempt_at,attempt_count,notified_at)
                   VALUES(?,?,?,1,NULL)
                   ON CONFLICT(operation_id,chat_id) DO UPDATE SET
                     last_attempt_at=excluded.last_attempt_at,
                     attempt_count=telegram_operation_notifications.attempt_count+1
                   WHERE telegram_operation_notifications.notified_at IS NULL""",
                (row["id"], chat_id, attempted_at),
            )
        try:
            client.send_message(config.telegram_bot_token, chat_id, message[:3900])
            with conn:
                conn.execute(
                    """UPDATE telegram_operation_notifications SET notified_at=?
                        WHERE operation_id=? AND chat_id=? AND notified_at IS NULL""",
                    (datetime.now(timezone.utc).isoformat(), row["id"], chat_id),
                )
        except Exception as exc:  # notification failure must never break the publishing worker
            if logger:
                logger.warning("BLACK SPIDER notification failed for operation %s/chat %s: %s",
                               row["id"], chat_id, redact(str(exc))[:300])
    remaining = conn.execute(
        """SELECT COUNT(*) FROM telegram_operation_notifications
            WHERE operation_id=? AND notified_at IS NOT NULL AND chat_id IN (%s)""" %
        ",".join("?" for _ in chats),
        (row["id"], *chats),
    ).fetchone()[0]
    if remaining == len(chats):
        with conn:
            conn.execute(
                "UPDATE telegram_operation_log SET notified_at=? WHERE id=? AND notified_at IS NULL",
                (datetime.now(timezone.utc).isoformat(), row["id"]),
            )


def record_publication_outcome(conn, publication_id: int, outcome, config, *,
                               dry_run: bool, logger=None, now=None):
    """Persist one outcome idempotently and notify for real success/retry/failure only."""
    row = conn.execute(
        """SELECT pub.id,pub.status,pub.attempt_count,pub.last_error,pub.remote_post_id,
                  pub.delivery_state,p.caption,c.account_name,c.platform
             FROM publications pub JOIN posts p ON p.id=pub.post_id
             JOIN channels c ON c.id=pub.channel_id WHERE pub.id=?""",
        (publication_id,),
    ).fetchone()
    if row is None or outcome.result == "skipped":
        return None
    if dry_run and outcome.result != "dry_run":
        # Validation can still reject a post during simulation. Record that result,
        # but never describe it as a real Instagram publish failure or alert it as one.
        status = "dry_run"
        detail = (
            "وضع المحاكاة نشط؛ لم تتم محاولة النشر على Instagram. "
            f"نتيجة الفحص التجريبي: {outcome.detail or outcome.result}"
        )
    elif outcome.result == "posted":
        status = "success"
        if row["delivery_state"] == "inbox":
            detail = (
                "تم تسليم الفيديو إلى صندوق النشر في المنصة؛ لم يتم تأكيد نشره على الحساب بعد. "
                f"المعرّف: {outcome.detail}"
            )
        else:
            detail = f"تم تأكيد النشر من المنصة. المعرّف: {row['remote_post_id'] or outcome.detail}"
    elif outcome.result == "dry_run":
        status = "dry_run"
        detail = "محاكاة فقط؛ لم يتم إرسال أي شيء إلى Instagram."
    elif outcome.result == "retry_scheduled":
        status = "retrying"
        detail = f"المحاولة {row['attempt_count']}؛ إعادة المحاولة مجدولة. {row['last_error'] or outcome.detail}"
    elif outcome.result == "failed":
        status = "failed"
        detail = row["last_error"] or outcome.detail
    elif outcome.result == "rate_limited":
        status = "retrying"
        detail = f"حد النشر لدى المنصة؛ أُجلت المحاولة. {outcome.detail}"
    else:
        status = "info"
        detail = outcome.detail
    event_key = f"publication:{publication_id}:{outcome.result}:{row['attempt_count']}"
    event_type = "تسليم لصندوق النشر" if outcome.result == "posted" and row["delivery_state"] == "inbox" else "نشر"
    event = _insert_event(
        conn, event_key=event_key, event_type=event_type, status=status,
        publication_id=publication_id, channel_name=row["account_name"],
        caption=row["caption"], detail=detail,
        created_at=(now or datetime.now(timezone.utc)).isoformat(),
    )
    _notify_once(conn, event, config, logger=logger)
    return event


def record_recovered_publication(conn, publication, config, *, logger=None, now=None):
    publication_id = int(publication["id"])
    row = conn.execute(
        """SELECT p.caption,c.account_name FROM publications pub
             JOIN posts p ON p.id=pub.post_id JOIN channels c ON c.id=pub.channel_id
            WHERE pub.id=?""", (publication_id,)
    ).fetchone()
    event = _insert_event(
        conn, event_key=f"publication:{publication_id}:recovered:{int(publication['attempt_count'])}",
        event_type="نشر", status="failed", publication_id=publication_id,
        channel_name=row["account_name"] if row else None,
        caption=row["caption"] if row else None,
        detail="توقف العامل أثناء تنفيذ النشر؛ لا يمكن الجزم هل وصل المنشور للمنصة. تحقق من الحساب قبل إعادة المحاولة.",
        created_at=(now or datetime.now(timezone.utc)).isoformat(),
    )
    _notify_once(conn, event, config, logger=logger)
    return event


def record_deferred_publication(conn, publication_id: int, config, detail: str, *,
                                logger=None, now=None):
    """Log a publication deferred before publish_one (for example, no public tunnel)."""
    row = conn.execute(
        """SELECT p.caption,c.account_name FROM publications pub
             JOIN posts p ON p.id=pub.post_id JOIN channels c ON c.id=pub.channel_id
            WHERE pub.id=?""", (publication_id,)
    ).fetchone()
    safe_detail = redact(str(detail or "تعذر تنفيذ المحاولة."))[:700]
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    event = _insert_event(
        conn,
        event_key=f"publication:{publication_id}:deferred:{stamp[:13]}",
        event_type="نشر", status="retrying", publication_id=publication_id,
        channel_name=row["account_name"] if row else None,
        caption=row["caption"] if row else None,
        detail=f"لم تبدأ عملية النشر؛ ستبقى في الطابور لإعادة المحاولة. {safe_detail}",
        created_at=stamp,
    )
    _notify_once(conn, event, config, logger=logger)
    return event


def record_manual_dm_outcome(conn, outbox_id: int, *, status: str, detail: str, now=None):
    """Add manually confirmed DM delivery results to the same operation history."""
    row = conn.execute(
        """SELECT o.channel_id,o.message_text,c.account_name
             FROM telegram_dm_outbox o JOIN channels c ON c.id=o.channel_id
            WHERE o.id=?""", (outbox_id,)
    ).fetchone()
    if row is None:
        return None
    if status not in {"success", "info", "failed"}:
        raise ValueError("unsupported manual DM operation status")
    return _insert_event(
        conn, event_key=f"dm-outbox:{outbox_id}:{status}",
        event_type="رسالة خاصة يدوية", status=status,
        channel_name=row["account_name"], caption=row["message_text"],
        detail=detail,
        created_at=(now or datetime.now(timezone.utc)).isoformat(),
    )


def retry_pending_notifications(conn, config, *, logger=None, limit: int = 20):
    """Retry durable alerts whose Telegram delivery was not confirmed previously."""
    if not config.telegram_bot_token or not _allowed(config.telegram_allowed_chat_ids):
        return 0
    retry_before = (
        datetime.now(timezone.utc) - timedelta(seconds=NOTIFICATION_RETRY_SECONDS)
    ).isoformat()
    eligibility = []
    parameters = []
    for chat_id in _allowed(config.telegram_allowed_chat_ids):
        eligibility.append(
            """(
                NOT EXISTS (
                    SELECT 1 FROM telegram_operation_notifications missing
                     WHERE missing.operation_id=event.id AND missing.chat_id=?
                )
                OR EXISTS (
                    SELECT 1 FROM telegram_operation_notifications pending
                     WHERE pending.operation_id=event.id AND pending.chat_id=?
                       AND pending.notified_at IS NULL
                       AND (pending.last_attempt_at IS NULL OR pending.last_attempt_at<=?)
                )
            )"""
        )
        parameters.extend((chat_id, chat_id, retry_before))
    rows = conn.execute(
        """SELECT event.* FROM telegram_operation_log event
            WHERE event.notified_at IS NULL AND event.status IN ('success','retrying','failed')
              AND (%s)
            ORDER BY event.created_at ASC,event.id ASC LIMIT ?""" % " OR ".join(eligibility),
        (*parameters, max(1, min(int(limit), 100))),
    ).fetchall()
    for row in rows:
        _notify_once(conn, row, config, logger=logger)
    delivered_after = conn.execute(
        """SELECT COUNT(*) FROM telegram_operation_log
            WHERE id IN (%s) AND notified_at IS NOT NULL""" %
        (",".join("?" for _ in rows) or "NULL"),
        tuple(row["id"] for row in rows),
    ).fetchone()[0] if rows else 0
    return delivered_after


def record_terminal_publication_failure(conn, publication_id: int, config, detail: str, *,
                                        logger=None, now=None):
    """Record a terminal failure detected before publish_one could return an outcome."""
    row = conn.execute(
        """SELECT pub.attempt_count,p.caption,c.account_name
             FROM publications pub JOIN posts p ON p.id=pub.post_id
             JOIN channels c ON c.id=pub.channel_id WHERE pub.id=?""",
        (publication_id,),
    ).fetchone()
    if row is None:
        return None
    event = _insert_event(
        conn, event_key=f"publication:{publication_id}:failed:{row['attempt_count']}",
        event_type="نشر", status="failed", publication_id=publication_id,
        channel_name=row["account_name"], caption=row["caption"],
        detail=detail or "فشل النشر قبل بدء المحاولة بسبب إعداد غير صالح.",
        created_at=(now or datetime.now(timezone.utc)).isoformat(),
    )
    _notify_once(conn, event, config, logger=logger)
    return event
