"""Unit tests for the opt-in comment automation poller."""
import json
import sqlite3
from datetime import datetime, timezone

from worker.comment_automation import run_comment_automation


class FakeClient:
    def __init__(self, comments):
        self.comments, self.private, self.public = comments, [], []

    def get_media_comments(self, media_id, token, **kwargs):
        return self.comments, None

    def send_private_reply(self, account_id, comment_id, message, token):
        self.private.append((account_id, comment_id, message))
        return {"message_id": "m-" + comment_id}

    def reply_to_comment(self, comment_id, message, token):
        self.public.append((comment_id, message))
        return "r-" + comment_id


def database():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE channels(id INTEGER PRIMARY KEY, platform TEXT, is_active INTEGER,
        access_token TEXT, remote_account_id TEXT, account_name TEXT);
      CREATE TABLE instagram_comment_rules(id INTEGER PRIMARY KEY, channel_id INTEGER,
        media_id TEXT, keywords_json TEXT, link_url TEXT, private_reply_text TEXT,
        public_reply_text TEXT, enabled INTEGER, last_polled_at TEXT, last_error TEXT);
      CREATE TABLE instagram_comment_events(comment_id TEXT PRIMARY KEY, rule_id INTEGER,
        channel_id INTEGER, media_id TEXT, comment_text TEXT, status TEXT,
        private_reply_id TEXT, public_reply_id TEXT, last_error TEXT,
        created_at TEXT, updated_at TEXT);
    """)
    conn.execute("INSERT INTO channels VALUES (1,'instagram',1,'token','ig-1','creator')")
    conn.execute("""INSERT INTO instagram_comment_rules
      VALUES (1,1,'reel-1',?,'https://example.com','Here {link}','Sent ❤️',1,NULL,NULL)""",
      (json.dumps(["تم", "الرابط"]),))
    conn.commit()
    return conn


def test_keyword_comment_sends_one_private_and_public_reply():
    conn = database()
    client = FakeClient([{"id":"c1","text":"ارسل الرابط","timestamp":"2026-11-17T10:00:00+00:00",
                          "from":{"id":"person-1","username":"person"}}])
    count = run_comment_automation(conn, None, datetime(2026,11,17,11,tzinfo=timezone.utc),
                                   client_for=lambda _: client)
    assert count == 1
    assert client.private == [("ig-1","c1","Here https://example.com")]
    assert client.public == [("c1","Sent ❤️")]
    assert conn.execute("SELECT status FROM instagram_comment_events").fetchone()[0] == "sent"


def test_duplicate_comment_is_not_sent_twice():
    conn = database()
    client = FakeClient([{"id":"c1","text":"تم","from":{"id":"person-1"}}])
    now = datetime(2026,11,17,11,tzinfo=timezone.utc)
    run_comment_automation(conn, None, now, client_for=lambda _: client)
    conn.execute("UPDATE instagram_comment_rules SET last_polled_at=NULL")
    conn.commit()
    run_comment_automation(conn, None, now, client_for=lambda _: client)
    assert len(client.private) == 1


def test_nonmatching_comment_is_marked_ignored():
    conn = database()
    client = FakeClient([{"id":"c2","text":"جميل","from":{"id":"person-2"}}])
    run_comment_automation(conn, None, datetime(2026,11,17,11,tzinfo=timezone.utc),
                           client_for=lambda _: client)
    assert conn.execute("SELECT status FROM instagram_comment_events").fetchone()[0] == "ignored"
    assert client.private == []


def test_own_comment_is_skipped():
    conn = database()
    client = FakeClient([{"id":"c3","text":"تم","from":{"id":"ig-1"}}])
    run_comment_automation(conn, None, datetime(2026,11,17,11,tzinfo=timezone.utc),
                           client_for=lambda _: client)
    assert conn.execute("SELECT COUNT(*) FROM instagram_comment_events").fetchone()[0] == 0

def test_private_replies_are_bounded_per_worker_cycle():
    conn = database()
    comments = [
        {"id": f"c{i}", "text": "تم", "from": {"id": f"person-{i}"}}
        for i in range(20)
    ]
    client = FakeClient(comments)
    run_comment_automation(
        conn, None, datetime(2026,11,17,11,tzinfo=timezone.utc),
        client_for=lambda _: client,
    )
    assert len(client.private) == 15
    assert conn.execute(
        "SELECT COUNT(*) FROM instagram_comment_events WHERE status='sent'"
    ).fetchone()[0] == 15


def test_private_replies_respect_account_hourly_safety_limit():
    conn = database()
    now = datetime(2026,11,17,11,tzinfo=timezone.utc)
    stamp = now.isoformat()
    conn.executemany(
        """INSERT INTO instagram_comment_events
           (comment_id, rule_id, channel_id, media_id, comment_text, status, created_at, updated_at)
           VALUES (?, 1, 1, 'reel-1', 'تم', 'sent', ?, ?)""",
        [(f"prior-{i}", stamp, stamp) for i in range(700)],
    )
    conn.commit()
    client = FakeClient([{"id":"new-comment","text":"تم","from":{"id":"person-1"}}])
    run_comment_automation(conn, None, now, client_for=lambda _: client)
    assert client.private == []
    assert conn.execute(
        "SELECT COUNT(*) FROM instagram_comment_events WHERE comment_id='new-comment'"
    ).fetchone()[0] == 0

