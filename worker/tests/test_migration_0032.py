"""Migration 0032: persistent state for Telegram schedule editing."""

def test_telegram_schedule_sessions_table_exists(conn):
    columns = {
        row["name"] for row in conn.execute(
            "PRAGMA table_info(telegram_schedule_sessions)"
        ).fetchall()
    }
    assert columns == {"chat_id", "publication_id", "timezone", "created_at"}


def test_schedule_session_is_removed_when_publication_is_deleted(conn):
    channel = conn.execute(
        "INSERT INTO channels(platform, account_name) VALUES ('instagram', 'test')"
    ).lastrowid
    post = conn.execute(
        "INSERT INTO posts(caption, post_type) VALUES ('caption', 'reel')"
    ).lastrowid
    publication = conn.execute(
        """INSERT INTO publications(post_id, channel_id, scheduled_at)
           VALUES (?, ?, '2099-01-01T00:00:00Z')""",
        (post, channel),
    ).lastrowid
    conn.execute(
        """INSERT INTO telegram_schedule_sessions(chat_id, publication_id, timezone, created_at)
           VALUES ('private-chat', ?, 'Asia/Aden', '2026-11-18T00:00:00+00:00')""",
        (publication,),
    )
    conn.commit()
    conn.execute("DELETE FROM publications WHERE id=?", (publication,))
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) FROM telegram_schedule_sessions WHERE chat_id='private-chat'"
    ).fetchone()[0] == 0
