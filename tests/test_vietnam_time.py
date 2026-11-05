"""Toàn hệ thống lưu, so sánh và hiển thị thời gian theo giờ Việt Nam."""

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from flask import g
from flask_migrate import downgrade, upgrade

from tests.support import csrf_for
from app import create_app, db
from app.models import AuditLog, Ban, MemberRecord, User, UserBan
from app.timeutil import club_now

ROOT = Path(__file__).resolve().parents[1]


class VietnamTimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "vietnam-time-secret-key-0123456789abc",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(self.directory.name) / 'time.db'}",
            "SCHEDULER_ENABLED": False})
        self.client = self.app.test_client()
        self.context = self.app.test_request_context()
        self.context.push()
        db.session.add(Ban(id=1, ten_ban="Kỹ thuật"))
        admin = User(id=1, mssv="SE180001", ho_ten="Chủ nhiệm", ngay_sinh=date(2003, 1, 1),
                     sdt="+84900000001", email="cn@example.test", status="approved", chuc_vu="CN",
                     email_confirmed=True)
        admin.set_password("test-password")
        db.session.add_all([admin, MemberRecord(user_id=1, ngay_gia_nhap=date(2025, 1, 1)),
                            UserBan(user_id=1, ban_id=1)])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.directory.cleanup()

    def test_timestamps_written_by_the_system_are_vietnam_time(self):
        utc = datetime.now(timezone.utc).replace(tzinfo=None)
        entry = AuditLog(action="test", object_type="User", object_id="1")
        db.session.add(entry)
        db.session.commit()
        self.assertLess(abs(entry.created_at - club_now()), timedelta(minutes=1))
        self.assertGreater(entry.created_at - utc, timedelta(hours=6, minutes=59))
        self.assertLess(abs(db.session.get(User, 1).password_changed_at - club_now()), timedelta(minutes=1))

    def test_early_morning_registration_shows_the_vietnam_date(self):
        applicant = User(id=2, mssv="SE190001", ho_ten="Người đăng ký", ngay_sinh=date(2005, 1, 1),
                         sdt="+84900000002", email="new@example.test", status="pending",
                         chuc_vu="THANH_VIEN", ngay_tao=datetime(2026, 11, 6, 1, 55))
        applicant.set_password("test-password")
        db.session.add(applicant)
        db.session.commit()
        with self.client.session_transaction() as session:
            session["_user_id"], session["_fresh"] = "1", True
        g.pop("_login_user", None)
        page = self.client.get("/admin/approve").get_data(as_text=True)
        self.assertIn("Nộp 06/11/2026", page)
        self.assertNotIn("Nộp 05/11/2026", page)

    def test_confirmation_link_lasts_one_hour(self):
        from app.routes.auth import send_confirmation_email
        user = db.session.get(User, 1)
        with patch("app.routes.auth.send_email", return_value=True) as mail:
            send_confirmation_email(user)
        self.assertIn("hiệu lực 1 giờ", mail.call_args.args[2])
        token = user.generate_token("confirm")
        self.assertIsNotNone(User.from_token(token, "confirm", self.app.config["EMAIL_CONFIRM_MAX_AGE"]))
        with patch("itsdangerous.timed.time.time", return_value=datetime.now().timestamp() + 3601):
            self.assertIsNone(User.from_token(token, "confirm", self.app.config["EMAIL_CONFIRM_MAX_AGE"]))


class VietnamTimeMigrationTests(unittest.TestCase):
    def test_migration_moves_stored_utc_values_to_vietnam_time_and_back(self):
        with tempfile.TemporaryDirectory() as folder:
            app = create_app({"TESTING": True, "AUTO_CREATE_TABLES": False, "SCHEDULER_ENABLED": False,
                              "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(folder) / 'migrate.db'}"})
            with app.app_context():
                upgrade(directory=str(ROOT / "migrations"), revision="0008")
                db.session.execute(db.text(
                    "INSERT INTO audit_log (action, object_type, object_id, details, created_at) "
                    "VALUES ('x', 'User', '1', '{}', '2026-11-05 18:55:00.123456')"))
                db.session.commit()

                def stored():
                    return db.session.execute(db.text("SELECT created_at FROM audit_log")).scalar()

                upgrade(directory=str(ROOT / "migrations"), revision="0009")
                self.assertEqual(stored(), "2026-11-06 01:55:00.123456")
                downgrade(directory=str(ROOT / "migrations"), revision="0008")
                self.assertEqual(stored(), "2026-11-05 18:55:00.123456")
                db.session.remove()
                db.engine.dispose()


if __name__ == "__main__":
    unittest.main()
