"""Tests for db-level due-selection helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
import threading
import time

from worker import db

NOW = datetime(2026, 7, 22, 18, 0, 0, tzinfo=timezone.utc)


def test_fetch_due_publications_excludes_held_rows(conn, make_publication):
    pub = make_publication(post_type="single", n_assets=1, now=NOW)

    conn.execute("UPDATE publications SET is_held = 1 WHERE id = ?", (pub["id"],))
    conn.commit()

    due = db.fetch_due_publications(conn, NOW.isoformat())
    assert pub["id"] not in [row["id"] for row in due]

    conn.execute("UPDATE publications SET is_held = 0 WHERE id = ?", (pub["id"],))
    conn.commit()

    due = db.fetch_due_publications(conn, NOW.isoformat())
    assert pub["id"] in [row["id"] for row in due]


def test_due_publications_break_ties_by_id(conn):
    """The slides of one Story share a scheduled_at and MUST go out in slide order.

    Ordering by scheduled_at alone leaves tie order up to SQLite's query plan, so this
    is a regression guard: slides are inserted in sort_order, which makes ascending id
    the publish order the worker has to preserve.
    """
    cid = conn.execute(
        "INSERT INTO channels (platform, account_name) VALUES ('instagram','IG')"
    ).lastrowid
    pid = conn.execute(
        "INSERT INTO posts (caption, post_type) VALUES ('x','carousel')"
    ).lastrowid
    when = "2026-07-22T17:00:00+00:00"
    ids = [
        conn.execute(
            "INSERT INTO publications (post_id, channel_id, scheduled_at, surface) "
            "VALUES (?,?,?,'story')",
            (pid, cid, when),
        ).lastrowid
        for _ in range(4)
    ]
    conn.commit()

    got = [r["id"] for r in db.fetch_due_publications(conn, NOW.isoformat())]
    assert got == ids, "story slides must publish in insertion (slide) order"


def test_database_connections_wait_for_transient_writer_contention(db_path):
    """Opening a connection and starting a write both tolerate a concurrent writer."""
    # Production enables WAL once in migrate.py before starting either process.
    setup = sqlite3.connect(str(db_path))
    setup.execute("PRAGMA journal_mode = WAL")
    setup.close()

    holder = sqlite3.connect(str(db_path), timeout=1, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    release = threading.Event()

    def release_writer():
        time.sleep(0.2)
        holder.rollback()
        holder.close()
        release.set()

    thread = threading.Thread(target=release_writer, daemon=True)
    thread.start()
    connection = None
    try:
        # This used to fail here because connect() re-issued PRAGMA journal_mode=WAL
        # while another connection held the writer lock.
        connection = db.connect(db_path)
        connection.execute("BEGIN IMMEDIATE")
        assert release.wait(timeout=2)
        connection.rollback()
    finally:
        if connection is not None:
            connection.close()
        thread.join(timeout=2)
