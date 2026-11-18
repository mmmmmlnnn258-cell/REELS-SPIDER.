import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "migrations"


def test_instagram_auth_method_migration_preserves_existing_channel_data(tmp_path):
    path = tmp_path / "before-0038.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda f: f.name):
        if sql_file.name >= "0038_instagram_auth_method.sql":
            break
        conn.executescript(sql_file.read_text())

    channel_id = conn.execute(
        """INSERT INTO channels
           (platform, account_name, timezone, remote_account_id, linked_page_id,
            access_token, token_expires_at, is_active)
           VALUES ('instagram', 'Existing account', 'Asia/Aden', 'ig-123',
                   'page-456', 'keep-this-token', '2027-01-01T00:00:00Z', 1)"""
    ).lastrowid
    conn.commit()

    migration = MIGRATIONS_DIR / "0038_instagram_auth_method.sql"
    conn.executescript(migration.read_text())

    row = conn.execute(
        """SELECT platform, account_name, timezone, remote_account_id, linked_page_id,
                  access_token, token_expires_at, is_active, instagram_auth_method
             FROM channels WHERE id = ?""",
        (channel_id,),
    ).fetchone()
    assert dict(row) == {
        "platform": "instagram",
        "account_name": "Existing account",
        "timezone": "Asia/Aden",
        "remote_account_id": "ig-123",
        "linked_page_id": "page-456",
        "access_token": "keep-this-token",
        "token_expires_at": "2027-01-01T00:00:00Z",
        "is_active": 1,
        "instagram_auth_method": "legacy",
    }

    new_id = conn.execute(
        "INSERT INTO channels(platform, account_name) VALUES ('instagram', 'New account')"
    ).lastrowid
    assert conn.execute(
        "SELECT instagram_auth_method FROM channels WHERE id = ?", (new_id,)
    ).fetchone()[0] == "legacy"

    try:
        conn.execute(
            "UPDATE channels SET instagram_auth_method = 'unknown' WHERE id = ?",
            (new_id,),
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("unknown Instagram auth methods must be rejected")

    conn.close()
