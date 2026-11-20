"""Cross-runtime token envelope and database-boundary tests."""
import base64
import os
import sqlite3

import pytest

from worker.token_crypto import decrypt_token, encrypt_token
from worker import db


KEY = base64.b64encode(bytes(range(32))).decode()


def test_round_trip_and_legacy_plaintext():
    encrypted = encrypt_token("IGAA-secret", KEY)
    assert encrypted.startswith("enc:v1:")
    assert decrypt_token(encrypted, KEY) == "IGAA-secret"
    assert decrypt_token("legacy-plain-token", KEY) == "legacy-plain-token"


def test_decrypts_shared_dashboard_aes_gcm_interoperability_fixture():
    fixture = (
        "enc:v1:AAECAwQFBgcICQoL:"
        "DkWXWuiGsHT+Mrr5xIcMBO6zqlKZAysJSgJYj58f/W9q6/R01Gu8gusy"
    )
    assert decrypt_token(fixture, KEY) == "IGAA-cross-runtime-fixture"


def test_missing_wrong_key_and_tampering_fail_closed(monkeypatch):
    encrypted = encrypt_token("IGAA-secret", KEY)
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError):
        decrypt_token(encrypted)
    with pytest.raises(RuntimeError):
        decrypt_token(encrypted, base64.b64encode(b"x" * 32).decode())
    parts = encrypted.split(":")
    parts[3] = base64.b64encode(b"tampered-ciphertext-and-tag").decode()
    with pytest.raises(RuntimeError):
        decrypt_token(":".join(parts), KEY)


def test_worker_database_decrypts_reads_and_encrypts_renewals(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", KEY)
    conn = db.connect(tmp_path / "tokens.sqlite")
    conn.execute("""CREATE TABLE channels (
        id INTEGER PRIMARY KEY, platform TEXT, instagram_auth_method TEXT,
        access_token TEXT
    )""")
    conn.execute(
        "INSERT INTO channels VALUES (1, 'instagram', 'instagram_login', ?)",
        (encrypt_token("first-token", KEY),),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM channels WHERE id = 1").fetchone()
    assert row["access_token"] == "first-token"
    db.update_channel(conn, 1, access_token="rotated-token")
    raw = sqlite3.connect(tmp_path / "tokens.sqlite")
    stored = raw.execute("SELECT access_token FROM channels WHERE id = 1").fetchone()[0]
    assert stored.startswith("enc:v1:")
    assert decrypt_token(stored, KEY) == "rotated-token"
    raw.close()
    conn.close()
