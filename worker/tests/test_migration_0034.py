from pathlib import Path
import sqlite3


def test_telegram_messaging_control_migration_creates_safe_sessions(conn):
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "telegram_comment_edit_sessions" in tables
    assert "telegram_dm_reply_sessions" in tables
    assert "telegram_dm_outbox" in tables
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_dm_outbox_rejects_unknown_status_values(conn):
    channel = conn.execute(
        "INSERT INTO channels(platform,account_name) VALUES('instagram','test')"
    ).lastrowid
    try:
        conn.execute(
            """INSERT INTO telegram_dm_outbox(chat_id,channel_id,conversation_id,
                 recipient_id,message_text,status,created_at,updated_at)
               VALUES('1',?,'c1','u1','hello','retry','now','now')""",
            (channel,),
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("outbox must reject unsupported delivery states")
