"""Migration 0033: persistent global publishing pause state."""


def test_publishing_control_is_seeded_active_by_default(conn):
    row = conn.execute(
        "SELECT id, is_paused, updated_at, updated_by_chat_id FROM publishing_control WHERE id=1"
    ).fetchone()
    assert row is not None
    assert row["is_paused"] == 0
    assert row["updated_at"]
    assert row["updated_by_chat_id"] is None


def test_publishing_control_accepts_only_boolean_pause_values(conn):
    conn.execute("UPDATE publishing_control SET is_paused=1 WHERE id=1")
    conn.commit()
    assert conn.execute(
        "SELECT is_paused FROM publishing_control WHERE id=1"
    ).fetchone()[0] == 1
    try:
        conn.execute("UPDATE publishing_control SET is_paused=2 WHERE id=1")
    except Exception:
        pass
    else:
        raise AssertionError("publishing_control must reject values other than 0 and 1")
