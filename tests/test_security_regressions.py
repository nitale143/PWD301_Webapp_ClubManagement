"""Regression coverage for authorization, logout, and SQLite schema drift."""
import importlib.util
import tempfile
import unittest
from datetime import date
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask import g
import sqlalchemy as sa

from tests.support import csrf_for, sign_csrf
from app import create_app, db
from app.models import Ban, MemberRecord, Role, User, UserBan
from app.services.access import can_issue_temp_password, set_member_left_safely


PASSWORD = "regression-password-2026"


class SecurityRegressionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.app = create_app({
            "TESTING": True,
            "SCHEDULER_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{root / 'security.db'}",
            "SECRET_KEY": "security-regression-test-key",
        })
        self.client = self.app.test_client()
        self.context = self.app.app_context()
        self.context.push()
        Role.insert_roles()
        db.session.add(Ban(id=1, ten_ban="Engineering"))
        users = (
            (1, "ADMIN", "ADMIN", False),
            (2, "CN001", "CN", True),
            (3, "TB001", "TB", True),
            (4, "MEMBER001", "THANH_VIEN", True),
        )
        for user_id, code, role, is_member in users:
            user = User(
                id=user_id,
                mssv=code,
                ho_ten=f"Member {user_id}",
                ngay_sinh=date(2003, 1, 1),
                sdt="+84912345678",
                email=f"member{user_id}@example.test",
                status="approved",
                chuc_vu=role,
            )
            user.set_password(PASSWORD)
            db.session.add(user)
            if is_member:
                db.session.add(MemberRecord(user_id=user_id, ngay_gia_nhap=date(2024, 1, 1)))
                db.session.add(UserBan(user_id=user_id, ban_id=1))
        db.session.commit()
        self.csrf_token = "security-regression-csrf"

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.directory.cleanup()

    @property
    def csrf_headers(self):
        return {"X-CSRF-Token": sign_csrf(self.app, self.csrf_token)}

    def login_as(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
            session["csrf_token"] = self.csrf_token
        g.pop("_login_user", None)

    def test_tb_cannot_change_higher_rank_or_system_admin_members(self):
        self.login_as(3)
        archived_admin = self.client.post("/api/v1/members/1/archive", headers=self.csrf_headers)
        self.assertEqual(archived_admin.status_code, 403)

        mark_cn_left = self.client.patch("/api/v1/members/2", json={"status": "left"},
                                         headers=self.csrf_headers)
        self.assertEqual(mark_cn_left.status_code, 403)
        self.assertEqual(db.session.get(MemberRecord, 2).tinh_trang, "active")

        admin = db.session.get(User, 1)
        self.assertFalse(can_issue_temp_password(db.session.get(User, 3), admin))
        temporary_password = self.client.post("/admin/members/1/temp-password",
                                              headers=self.csrf_headers)
        self.assertEqual(temporary_password.status_code, 403)

    def test_remember_logout_clears_cookie_and_profile_requires_login(self):
        client = self.app.test_client()
        self.assertEqual(client.get("/login").status_code, 200)
        login = client.post("/login", data={
            "mssv": "TB001",
            "password": PASSWORD,
            "remember": "y",
            "csrf_token": csrf_for(client),
        })
        self.assertEqual(login.status_code, 302)
        remember_cookie = [value for value in login.headers.getlist("Set-Cookie")
                           if value.startswith("fia_remember=")]
        self.assertEqual(len(remember_cookie), 1)
        self.assertNotEqual(remember_cookie[0].split(";", 1)[0], "fia_remember=")

        logout = client.post("/logout", data={"csrf_token": csrf_for(client)})
        self.assertEqual(logout.status_code, 302)
        self.assertEqual(logout.headers["Location"], "/login")
        cleared_cookie = "\n".join(logout.headers.getlist("Set-Cookie"))
        self.assertRegex(cleared_cookie, r"fia_remember=;.*(?:Max-Age=0|Expires=Thu, 01 Jan 1970)")

        g.pop("_login_user", None)
        profile = client.get("/profile")
        self.assertEqual(profile.status_code, 302)
        self.assertIn("/login", profile.headers["Location"])

    def test_concurrent_promotion_invalidates_manager_archive_check(self):
        manager = db.session.get(User, 3)
        target = db.session.get(User, 4)
        record = db.session.get(MemberRecord, 4)
        # Emulate another transaction promoting the cached target after authorization.
        with db.engine.begin() as connection:
            connection.execute(sa.update(User).where(User.id == 4).values(chuc_vu="CN"))
        self.assertEqual(target.chuc_vu, "THANH_VIEN")
        self.assertFalse(set_member_left_safely(target, record, actor=manager))
        db.session.rollback()
        self.assertEqual(db.session.get(MemberRecord, 4).tinh_trang, "active")


class ChatConstraintMigrationTests(unittest.TestCase):
    def test_upgrade_names_legacy_unique_without_losing_data_or_foreign_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "legacy-chat.db"
            engine = sa.create_engine(f"sqlite:///{database_path}")
            with engine.begin() as connection:
                connection.exec_driver_sql("CREATE TABLE user (id INTEGER PRIMARY KEY)")
                connection.exec_driver_sql(
                    "CREATE TABLE chat_conversation ("
                    "id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title VARCHAR(100) NOT NULL, "
                    "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, "
                    "archived BOOLEAN NOT NULL DEFAULT 0, legacy_marker VARCHAR(16), "
                    "FOREIGN KEY(user_id) REFERENCES user(id), UNIQUE(user_id, legacy_marker))"
                )
                connection.exec_driver_sql(
                    "CREATE INDEX ix_chat_conversation_owner_archived_updated "
                    "ON chat_conversation(user_id, archived, updated_at, id)"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE chat_message (id INTEGER PRIMARY KEY, "
                    "conversation_id INTEGER REFERENCES chat_conversation(id))"
                )
                connection.exec_driver_sql("INSERT INTO user (id) VALUES (1)")
                connection.exec_driver_sql(
                    "INSERT INTO chat_conversation "
                    "(id, user_id, title, created_at, updated_at, archived, legacy_marker) "
                    "VALUES (10, 1, 'Preserved', '2026-10-20', '2026-10-20', 0, 'legacy')"
                )
                connection.exec_driver_sql("INSERT INTO chat_message (id, conversation_id) VALUES (20, 10)")

            migration_path = Path(__file__).parents[1] / "migrations" / "versions" / "0004_chat_constraint.py"
            specification = importlib.util.spec_from_file_location("migration_0004_chat_constraint", migration_path)
            migration = importlib.util.module_from_spec(specification)
            specification.loader.exec_module(migration)
            with engine.connect() as connection:
                for _ in range(2):
                    context = MigrationContext.configure(connection)
                    with connection.begin():
                        with Operations.context(context):
                            migration.upgrade()
                inspector = sa.inspect(connection)
                constraints = inspector.get_unique_constraints("chat_conversation")
                self.assertEqual(
                    [(constraint["name"], constraint["column_names"]) for constraint in constraints],
                    [("uq_chat_conversation_legacy", ["user_id", "legacy_marker"])],
                )
                self.assertEqual(connection.exec_driver_sql(
                    "SELECT id, user_id, title, legacy_marker FROM chat_conversation"
                ).one(), (10, 1, "Preserved", "legacy"))
                self.assertEqual(connection.exec_driver_sql(
                    "SELECT id, conversation_id FROM chat_message"
                ).one(), (20, 10))
                self.assertEqual(connection.exec_driver_sql(
                    "PRAGMA foreign_key_list(chat_conversation)"
                ).fetchall()[0][2], "user")
                self.assertEqual(connection.exec_driver_sql(
                    "PRAGMA foreign_key_list(chat_message)"
                ).fetchall()[0][2], "chat_conversation")
                self.assertEqual(connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall(), [])
                self.assertIn("ix_chat_conversation_owner_archived_updated",
                              {index["name"] for index in inspector.get_indexes("chat_conversation")})
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
