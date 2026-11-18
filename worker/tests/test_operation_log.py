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
