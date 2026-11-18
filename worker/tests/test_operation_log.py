from dataclasses import replace
from types import SimpleNamespace

from worker import operation_log


class FakeTelegram:
    def __init__(self):
        self.messages = []

    def send_message(self, token, chat_id, message):
        self.messages.append((token, chat_id, message))
        return {"message_id": len(self.messages)}


def test_success_is_logged_and_notified_once(conn, config, make_publication, monkeypatch):
    pub = make_publication(post_type="single", n_assets=1)
    conn.execute(
        "UPDATE publications SET status='posted',remote_post_id='ig-media-1',attempt_count=1 WHERE id=?",
        (pub["id"],),
    )
    conn.commit()
    telegram = FakeTelegram()
    monkeypatch.setattr(operation_log, "_telegram_client", lambda: telegram)
    cfg = replace(config, telegram_bot_token="test-token",
                  telegram_allowed_chat_ids="7441755677")
    outcome = SimpleNamespace(result="posted", detail="ig-media-1")

    first = operation_log.record_publication_outcome(
        conn, pub["id"], outcome, cfg, dry_run=False
    )
    second = operation_log.record_publication_outcome(
        conn, pub["id"], outcome, cfg, dry_run=False
    )

    assert first["status"] == "success"
    assert second["id"] == first["id"]
    assert len(telegram.messages) == 1
    assert "تم النشر بنجاح" in telegram.messages[0][2]
    assert "ig-media-1" in telegram.messages[0][2]
    assert conn.execute("SELECT COUNT(*) FROM telegram_operation_log").fetchone()[0] == 1
    assert conn.execute(
        "SELECT notified_at FROM telegram_operation_log WHERE id=?", (first["id"],)
    ).fetchone()[0]


def test_retry_and_terminal_failure_have_distinct_alerts(conn, config, make_publication, monkeypatch):
    pub = make_publication(post_type="single", n_assets=1)
    conn.execute(
        "UPDATE publications SET status='scheduled',attempt_count=1,last_error='temporary API error' WHERE id=?",
        (pub["id"],),
    )
    conn.commit()
    telegram = FakeTelegram()
    monkeypatch.setattr(operation_log, "_telegram_client", lambda: telegram)
    cfg = replace(config, telegram_bot_token="test-token",
                  telegram_allowed_chat_ids="7441755677")
    retry = SimpleNamespace(result="retry_scheduled", detail="retry")
    operation_log.record_publication_outcome(conn, pub["id"], retry, cfg, dry_run=False)
    conn.execute(
        "UPDATE publications SET status='failed',attempt_count=3,last_error='permanent API error' WHERE id=?",
        (pub["id"],),
    )
    conn.commit()
    failed = SimpleNamespace(result="failed", detail="failed")
    operation_log.record_publication_outcome(conn, pub["id"], failed, cfg, dry_run=False)

    assert len(telegram.messages) == 2
    assert "ستتم إعادة المحاولة" in telegram.messages[0][2]
    assert "فشل النشر نهائياً" in telegram.messages[1][2]
    assert conn.execute(
        "SELECT COUNT(*) FROM telegram_operation_log WHERE status IN ('retrying','failed')"
    ).fetchone()[0] == 2


def test_dry_run_is_logged_without_claiming_real_publish_or_alerting(conn, config, make_publication, monkeypatch):
    pub = make_publication(post_type="single", n_assets=1)
    conn.execute(
        "UPDATE publications SET status='posted',is_dry_run=1,remote_post_id='DRYRUN' WHERE id=?",
        (pub["id"],),
    )
    conn.commit()
    telegram = FakeTelegram()
    monkeypatch.setattr(operation_log, "_telegram_client", lambda: telegram)
    cfg = replace(config, telegram_bot_token="test-token",
                  telegram_allowed_chat_ids="7441755677")
    event = operation_log.record_publication_outcome(
        conn, pub["id"], SimpleNamespace(result="dry_run", detail="dry"),
        cfg, dry_run=True
    )

    assert event["status"] == "dry_run"
    assert "محاكاة فقط" in event["detail"]
    assert telegram.messages == []


def test_notification_failure_does_not_escape_worker(conn, config, make_publication, monkeypatch):
    pub = make_publication(post_type="single", n_assets=1)
    conn.execute(
        "UPDATE publications SET status='posted',remote_post_id='ig-media-2' WHERE id=?",
        (pub["id"],),
    )
    conn.commit()
    class BrokenTelegram:
        def send_message(self, *args, **kwargs):
            raise RuntimeError("Telegram offline")
    monkeypatch.setattr(operation_log, "_telegram_client", lambda: BrokenTelegram())
    cfg = replace(config, telegram_bot_token="test-token",
                  telegram_allowed_chat_ids="7441755677")
    event = operation_log.record_publication_outcome(
        conn, pub["id"], SimpleNamespace(result="posted", detail="ok"),
        cfg, dry_run=False
    )
    assert event["status"] == "success"
    assert conn.execute(
        "SELECT COUNT(*) FROM telegram_operation_log"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT notified_at FROM telegram_operation_log"
    ).fetchone()[0] is None


def test_deferred_attempt_is_logged_and_notified_once(conn, config, make_publication, monkeypatch):
    pub = make_publication(post_type="single", n_assets=1)
    telegram = FakeTelegram()
    monkeypatch.setattr(operation_log, "_telegram_client", lambda: telegram)
    cfg = replace(config, telegram_bot_token="test-token",
                  telegram_allowed_chat_ids="7441755677")
    first = operation_log.record_deferred_publication(
        conn, pub["id"], cfg, "publish endpoint unavailable"
    )
    second = operation_log.record_deferred_publication(
        conn, pub["id"], cfg, "publish endpoint unavailable"
    )
    assert first["status"] == "retrying"
    assert second["id"] == first["id"]
    assert len(telegram.messages) == 1
    assert "ستتم إعادة المحاولة" in telegram.messages[0][2]


def test_manual_dm_outcome_appears_in_shared_operation_log(conn, config):
    channel = conn.execute(
        "INSERT INTO channels(platform,account_name) VALUES('instagram','رسائل القناة')"
    ).lastrowid
    outbox = conn.execute(
        """INSERT INTO telegram_dm_outbox(chat_id,channel_id,conversation_id,recipient_id,
             message_text,status,created_at,updated_at)
           VALUES('7441755677',?,'conversation-1','peer-1','رد يدوي','sent','now','now')""",
        (channel,),
    ).lastrowid
    conn.commit()
    event = operation_log.record_manual_dm_outcome(
        conn, outbox, status="success", detail="Meta message id: m-1"
    )
    assert event["event_type"] == "رسالة خاصة يدوية"
    assert event["status"] == "success"
    assert event["channel_name"] == "رسائل القناة"
    assert event["caption"] == "رد يدوي"


def test_inbox_delivery_is_not_mislabeled_as_published(conn, config, make_publication, monkeypatch):
    pub = make_publication(post_type="video", n_assets=1)
    conn.execute(
        "UPDATE publications SET status='posted',delivery_state='inbox',remote_post_id=NULL,remote_container_id='upload-1' WHERE id=?",
        (pub["id"],),
    )
    conn.commit()
    telegram = FakeTelegram()
    monkeypatch.setattr(operation_log, "_telegram_client", lambda: telegram)
    cfg = replace(config, telegram_bot_token="test-token",
                  telegram_allowed_chat_ids="7441755677")
    event = operation_log.record_publication_outcome(
        conn, pub["id"], SimpleNamespace(result="posted", detail="upload-1"),
        cfg, dry_run=False
    )
    assert event["event_type"] == "تسليم لصندوق النشر"
    assert "لم يتم تأكيد نشره" in event["detail"]
    assert "لم يتم نشره على الحساب بعد" in telegram.messages[0][2]


def test_failed_telegram_alert_is_retried_on_later_worker_cycle(conn, config, make_publication, monkeypatch):
    pub = make_publication(post_type="single", n_assets=1)
    conn.execute(
        "UPDATE publications SET status='posted',remote_post_id='ig-media-3' WHERE id=?",
        (pub["id"],),
    )
    conn.commit()
    class BrokenTelegram:
        def send_message(self, *args, **kwargs):
            raise RuntimeError("temporary Telegram outage")
    monkeypatch.setattr(operation_log, "_telegram_client", lambda: BrokenTelegram())
    cfg = replace(config, telegram_bot_token="test-token",
                  telegram_allowed_chat_ids="7441755677")
    operation_log.record_publication_outcome(
        conn, pub["id"], SimpleNamespace(result="posted", detail="ok"),
        cfg, dry_run=False
    )
    assert conn.execute(
        "SELECT notified_at FROM telegram_operation_log"
    ).fetchone()[0] is None

    telegram = FakeTelegram()
    monkeypatch.setattr(operation_log, "_telegram_client", lambda: telegram)
    from datetime import datetime, timedelta, timezone
    old_attempt = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    conn.execute(
        "UPDATE telegram_operation_notifications SET last_attempt_at=?",
        (old_attempt,),
    )
    conn.commit()
    assert operation_log.retry_pending_notifications(conn, cfg) == 1
    assert len(telegram.messages) == 1
    assert conn.execute(
        "SELECT notified_at FROM telegram_operation_log"
    ).fetchone()[0]


def test_partial_multi_chat_delivery_retries_only_the_missing_recipient(conn, config, make_publication, monkeypatch):
    pub = make_publication(post_type="single", n_assets=1)
    conn.execute(
        "UPDATE publications SET status='posted',attempt_count=1,remote_post_id='ig-media-multi' WHERE id=?",
        (pub["id"],),
    )
    conn.commit()

    class PartiallyFailingTelegram:
        def __init__(self):
            self.calls = []
            self.failed_second = False

        def send_message(self, token, chat_id, message):
            self.calls.append(chat_id)
            if chat_id == "222" and not self.failed_second:
                self.failed_second = True
                raise RuntimeError("temporary outage for second chat")
            return {"message_id": len(self.calls)}

    telegram = PartiallyFailingTelegram()
    monkeypatch.setattr(operation_log, "_telegram_client", lambda: telegram)
    cfg = replace(config, telegram_bot_token="test-token",
                  telegram_allowed_chat_ids="111,222")
    outcome = SimpleNamespace(result="posted", detail="ig-media-multi")
    event = operation_log.record_publication_outcome(
        conn, pub["id"], outcome, cfg, dry_run=False
    )
    assert telegram.calls == ["111", "222"]
    assert event["notified_at"] is None
    from datetime import datetime, timedelta, timezone
    old_attempt = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    conn.execute(
        "UPDATE telegram_operation_notifications SET last_attempt_at=? WHERE operation_id=? AND chat_id='222'",
        (old_attempt, event["id"]),
    )
    conn.commit()

    operation_log.retry_pending_notifications(conn, cfg)
    assert telegram.calls == ["111", "222", "222"]
    assert conn.execute(
        "SELECT COUNT(*) FROM telegram_operation_notifications WHERE operation_id=?",
        (event["id"],),
    ).fetchone()[0] == 2
    assert conn.execute(
        "SELECT notified_at FROM telegram_operation_log WHERE id=?", (event["id"],)
    ).fetchone()[0]


def test_deferred_errors_are_deduplicated_per_publication_and_hour(conn, config, make_publication):
    from datetime import datetime, timezone

    pub = make_publication(post_type="single", n_assets=1)
    first = operation_log.record_deferred_publication(
        conn, pub["id"], config, "cloudflared temporary error",
        now=datetime(2026, 11, 18, 10, 1, tzinfo=timezone.utc),
    )
    second = operation_log.record_deferred_publication(
        conn, pub["id"], config, "cloudflared different transient detail",
        now=datetime(2026, 11, 18, 10, 45, tzinfo=timezone.utc),
    )
    third = operation_log.record_deferred_publication(
        conn, pub["id"], config, "cloudflared still unavailable",
        now=datetime(2026, 11, 18, 11, 1, tzinfo=timezone.utc),
    )
    assert first["id"] == second["id"]
    assert third["id"] != first["id"]


def test_repeated_recovery_after_a_new_attempt_is_logged_again(conn, config, make_publication):
    pub = make_publication(post_type="single", n_assets=1)
    row = conn.execute(
        "SELECT * FROM publications WHERE id=?", (pub["id"],)
    ).fetchone()
    first = operation_log.record_recovered_publication(conn, row, config)
    conn.execute(
        "UPDATE publications SET attempt_count=attempt_count+1 WHERE id=?", (pub["id"],)
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM publications WHERE id=?", (pub["id"],)
    ).fetchone()
    second = operation_log.record_recovered_publication(conn, row, config)
    assert first["id"] != second["id"]
    assert conn.execute(
        "SELECT COUNT(*) FROM telegram_operation_log WHERE publication_id=? AND status='failed'",
        (pub["id"],),
    ).fetchone()[0] == 2


def test_dry_run_validation_failure_is_not_reported_as_live_publish_failure(
    conn, config, make_publication, monkeypatch
):
    pub = make_publication(post_type="single", n_assets=1)
    telegram = FakeTelegram()
    monkeypatch.setattr(operation_log, "_telegram_client", lambda: telegram)
    cfg = replace(config, telegram_bot_token="test-token",
                  telegram_allowed_chat_ids="7441755677")
    event = operation_log.record_publication_outcome(
        conn, pub["id"], SimpleNamespace(result="failed", detail="invalid test media"),
        cfg, dry_run=True
    )
    assert event["status"] == "dry_run"
    assert "لم تتم محاولة النشر على Instagram" in event["detail"]
    assert telegram.messages == []


def test_old_failed_alerts_do_not_starve_newer_notifications(conn, config, monkeypatch):
    telegram = FakeTelegram()

    class FailingOldAlerts:
        def send_message(self, token, chat_id, message):
            if "event-20" not in message:
                raise RuntimeError("blocked recipient for old event")
            return telegram.send_message(token, chat_id, message)

    monkeypatch.setattr(operation_log, "_telegram_client", lambda: FailingOldAlerts())
    cfg = replace(config, telegram_bot_token="test-token",
                  telegram_allowed_chat_ids="7441755677")
    for i in range(21):
        operation_log._insert_event(
            conn, event_key=f"starvation-{i}", event_type="نشر", status="success",
            caption=f"event-{i}", detail="success",
            created_at=f"2026-11-18T12:{i:02d}:00+00:00",
        )

    assert operation_log.retry_pending_notifications(conn, cfg, limit=20) == 0
    # The first 20 failed recipients are in backoff; the 21st event must be selected
    # on the next poll rather than being starved behind the same oldest failures.
    assert operation_log.retry_pending_notifications(conn, cfg, limit=20) == 1
    assert len(telegram.messages) == 1
    assert "event-20" in telegram.messages[0][2]
    assert conn.execute(
        "SELECT notified_at FROM telegram_operation_log WHERE event_key='starvation-20'"
    ).fetchone()[0]


def test_retry_recovers_recipient_not_attempted_before_worker_crash(conn, config, monkeypatch):
    telegram = FakeTelegram()
    monkeypatch.setattr(operation_log, "_telegram_client", lambda: telegram)
    cfg = replace(config, telegram_bot_token="test-token",
                  telegram_allowed_chat_ids="111,222")
    event = operation_log._insert_event(
        conn, event_key="crash-between-recipients", event_type="نشر",
        status="success", caption="crash recovery", detail="confirmed",
    )
    conn.execute(
        """INSERT INTO telegram_operation_notifications
           (operation_id,chat_id,last_attempt_at,attempt_count,notified_at)
           VALUES(?,?,?,?,?)""",
        (event["id"], "111", "2026-11-18T10:00:00+00:00", 1,
         "2026-11-18T10:00:01+00:00"),
    )
    conn.commit()

    assert operation_log.retry_pending_notifications(conn, cfg) == 1
    assert [message[1] for message in telegram.messages] == ["222"]
    assert conn.execute(
        "SELECT notified_at FROM telegram_operation_log WHERE id=?", (event["id"],)
    ).fetchone()[0]


def test_dry_run_failure_does_not_hide_later_live_failure(conn, config, make_publication, monkeypatch):
    pub = make_publication(post_type="single", n_assets=1)
    telegram = FakeTelegram()
    monkeypatch.setattr(operation_log, "_telegram_client", lambda: telegram)
    cfg = replace(config, telegram_bot_token="test-token",
                  telegram_allowed_chat_ids="7441755677")
    simulated = operation_log.record_publication_outcome(
        conn, pub["id"], SimpleNamespace(result="failed", detail="invalid test media"),
        cfg, dry_run=True
    )
    live = operation_log.record_publication_outcome(
        conn, pub["id"], SimpleNamespace(result="failed", detail="Meta rejected publish"),
        cfg, dry_run=False
    )
    assert simulated["status"] == "dry_run"
    assert live["status"] == "failed"
    assert live["id"] != simulated["id"]
    assert len(telegram.messages) == 1
    assert "فشل النشر نهائياً" in telegram.messages[0][2]


def test_manual_dm_success_alert_has_dm_specific_text_and_can_be_retried(conn, config, monkeypatch):
    channel = conn.execute(
        "INSERT INTO channels(platform,account_name) VALUES('instagram','DM channel')"
    ).lastrowid
    outbox = conn.execute(
        """INSERT INTO telegram_dm_outbox(chat_id,channel_id,conversation_id,recipient_id,
             message_text,status,created_at,updated_at)
           VALUES('7441755677',?,'conversation-1','peer-1','رد خاص','sent','now','now')""",
        (channel,),
    ).lastrowid
    conn.commit()
    event = operation_log.record_manual_dm_outcome(
        conn, outbox, status="success", detail="Meta confirmed message id m-2"
    )
    telegram = FakeTelegram()
    monkeypatch.setattr(operation_log, "_telegram_client", lambda: telegram)
    cfg = replace(config, telegram_bot_token="test-token",
                  telegram_allowed_chat_ids="7441755677")
    assert operation_log.retry_pending_notifications(conn, cfg) == 1
    assert "أكدت Meta إرسال الرد اليدوي بنجاح" in telegram.messages[0][2]
    assert "نص الرد: رد خاص" in telegram.messages[0][2]
    assert conn.execute(
        "SELECT notified_at FROM telegram_operation_log WHERE id=?", (event["id"],)
    ).fetchone()[0]


def test_unknown_manual_dm_outcome_is_retried_with_uncertainty_wording(conn, config, monkeypatch):
    channel = conn.execute(
        "INSERT INTO channels(platform,account_name) VALUES('instagram','DM channel')"
    ).lastrowid
    outbox = conn.execute(
        """INSERT INTO telegram_dm_outbox(chat_id,channel_id,conversation_id,recipient_id,
             message_text,status,created_at,updated_at)
           VALUES('7441755677',?,'conversation-1','peer-1','رد غير مؤكد','unknown','now','now')""",
        (channel,),
    ).lastrowid
    conn.commit()
    event = operation_log.record_manual_dm_outcome(
        conn, outbox, status="info", detail="نتيجة الإرسال غير مؤكدة؛ لا تعاود الإرسال."
    )
    telegram = FakeTelegram()
    monkeypatch.setattr(operation_log, "_telegram_client", lambda: telegram)
    cfg = replace(config, telegram_bot_token="test-token",
                  telegram_allowed_chat_ids="7441755677")
    assert operation_log.retry_pending_notifications(conn, cfg) == 1
    assert "نتيجة إرسال الرد اليدوي غير مؤكدة" in telegram.messages[0][2]
    assert conn.execute(
        "SELECT notified_at FROM telegram_operation_log WHERE id=?", (event["id"],)
    ).fetchone()[0]


def test_operation_log_redacts_credentials_from_caption_text(conn, config):
    channel = conn.execute(
        "INSERT INTO channels(platform,account_name) VALUES('instagram','safe channel')"
    ).lastrowid
    secret_text = "message contains EAA" + "A" * 28
    outbox = conn.execute(
        """INSERT INTO telegram_dm_outbox(chat_id,channel_id,conversation_id,recipient_id,
             message_text,status,created_at,updated_at)
           VALUES('7441755677',?,'conversation-1','peer-1',?,'sent','now','now')""",
        (channel, secret_text),
    ).lastrowid
    conn.commit()
    event = operation_log.record_manual_dm_outcome(
        conn, outbox, status="success", detail="Meta confirmed"
    )
    assert "EAA" not in event["caption"]
    assert "<redacted>" in event["caption"]
