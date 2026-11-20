import sqlite3
import unittest
from pathlib import Path


class TenantIdentitySchemaTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("PRAGMA foreign_keys = ON")
        migration = Path(__file__).resolve().parents[1] / "migrations" / "0039_tenant_identity_foundation.sql"
        self.db.executescript(migration.read_text(encoding="utf-8"))

    def tearDown(self):
        self.db.close()

    def seed_membership(self, email="owner@example.test", slug="alpha"):
        tenant_id = self.db.execute(
            "INSERT INTO tenants(name, slug) VALUES (?, ?)", (slug.title(), slug)
        ).lastrowid
        user_id = self.db.execute(
            "INSERT INTO users(email, password_hash) VALUES (?, ?)",
            (email, "scrypt$v1$test-hash-that-is-not-a-password"),
        ).lastrowid
        self.db.execute(
            "INSERT INTO tenant_memberships(tenant_id, user_id, role) VALUES (?, ?, 'owner')",
            (tenant_id, user_id),
        )
        return tenant_id, user_id

    def test_session_requires_membership_and_stores_digest_shape(self):
        tenant_a, user_a = self.seed_membership()
        tenant_b = self.db.execute(
            "INSERT INTO tenants(name, slug) VALUES ('Beta', 'beta')"
        ).lastrowid
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO tenant_sessions(tenant_id, user_id, token_hash, expires_at) VALUES (?, ?, ?, '2099-01-01')",
                (tenant_b, user_a, "a" * 64),
            )
        self.db.execute(
            "INSERT INTO tenant_sessions(tenant_id, user_id, token_hash, expires_at) VALUES (?, ?, ?, '2099-01-01')",
            (tenant_a, user_a, "b" * 64),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO tenant_sessions(tenant_id, user_id, token_hash, expires_at) VALUES (?, ?, ?, '2099-01-01')",
                (tenant_a, user_a, "NOT-A-SHA256-DIGEST"),
            )

    def test_telegram_chat_is_bound_to_membership_and_unique(self):
        tenant_a, user_a = self.seed_membership()
        tenant_b, _ = self.seed_membership(email="other@example.test", slug="beta")
        self.db.execute(
            "INSERT INTO tenant_telegram_chats(tenant_id, chat_id, linked_by_user_id) VALUES (?, '12345', ?)",
            (tenant_a, user_a),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO tenant_telegram_chats(tenant_id, chat_id, linked_by_user_id) VALUES (?, '12345', ?)",
                (tenant_b, user_a),
            )

    def test_email_and_membership_uniqueness_are_case_insensitive_and_scoped(self):
        tenant_a, _ = self.seed_membership()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO users(email, password_hash) VALUES ('OWNER@example.test', 'scrypt$v1$another-test-hash')"
            )
        user_b = self.db.execute(
            "INSERT INTO users(email, password_hash) VALUES ('member@example.test', 'scrypt$v1$member-test-hash')"
        ).lastrowid
        self.db.execute(
            "INSERT INTO tenant_memberships(tenant_id, user_id, role) VALUES (?, ?, 'member')",
            (tenant_a, user_b),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "INSERT INTO tenant_memberships(tenant_id, user_id, role) VALUES (?, ?, 'admin')",
                (tenant_a, user_b),
            )


if __name__ == "__main__":
    unittest.main()
