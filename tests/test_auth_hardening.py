import io
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from flask import g

from tests.support import csrf_for
from app import create_app, db
from app.models import Ban, User, UserBan
from app.mail import send_email


class AuthHardeningTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.app = create_app({
            "TESTING": True,
            "SCHEDULER_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{self.root / 'auth-test.db'}",
            "SECRET_KEY": "isolated-test-secret-key",
            "LOGIN_MAX_ATTEMPTS": 5,
            "LOGIN_LOCKOUT_SECONDS": 300,
            "LOGIN_LIMIT_ENABLED": True,
            "UPLOAD_FOLDER": str(self.root / "avatars"),
            "ALLOWED_AVATAR_EXTENSIONS": {"png", "jpg", "jpeg", "gif", "webp"},
        })
        self.client = self.app.test_client()
        self.context = self.app.app_context()
        self.context.push()
        db.session.add(Ban(id=1, ten_ban="Ky thuat"))
        for user_id, code, status in (
            (1, "STU001", "approved"),
            (2, "STU002", "pending"),
            (3, "STU003", "rejected"),
        ):
            user = User(
                id=user_id,
                mssv=code,
                ho_ten=f"Member {user_id}",
                ngay_sinh=date(2003, 1, 1),
                sdt="0912345678",
                email=f"member{user_id}@example.test",
                status=status,
                chuc_vu="THANH_VIEN",
            )
            user.set_password("fixture-ui-test-password")
            db.session.add(user)
            db.session.add(UserBan(user_id=user_id, ban_id=1))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.context.pop()
        self.directory.cleanup()

    def csrf(self, client=None):
        client = client or self.client
        response = client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="csrf_token"', response.data)
        return csrf_for(client)

    def login(self, client=None, code="STU001", password="fixture-ui-test-password", **extra):
        client = client or self.client
        token = self.csrf(client)
        return client.post("/login", data={
            "mssv": code,
            "password": password,
            "csrf_token": token,
            **extra,
        })

    def test_csrf_guards_every_unsafe_method_even_with_an_untrusted_origin(self):
        self.csrf()
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for data in ({}, {"csrf_token": "invalid"}):
                response = self.client.open(
                    "/not-a-route",
                    method=method,
                    data=data,
                    headers={"Origin": "https://attacker.example"},
                )
                self.assertEqual(response.status_code, 400, (method, data))

        token = self.csrf()
        non_ascii = self.client.post("/not-a-route", data={"csrf_token": "mật"})
        self.assertEqual(non_ascii.status_code, 400)
        response = self.client.post("/not-a-route", headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 404)

        for path in ("/login", "/register", "/profile/task-preferences", "/profile/avatar",
                     "/funds/collections/new", "/api/v1/assistant"):
            missing = self.client.post(path)
            self.assertEqual(missing.status_code, 400, path)

    def test_login_is_generic_rate_limited_rotates_session_and_uses_only_local_next(self):
        token = self.csrf()
        unknown = self.client.post("/login", data={
            "mssv": "UNKNOWN1", "password": "wrong-password", "csrf_token": token,
        })
        rejected = self.client.post("/login", data={
            "mssv": "STU003", "password": "fixture-ui-test-password", "csrf_token": token,
        })
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(rejected.status_code, 200)
        self.assertIn("MSSV hoặc mật khẩu không đúng.".encode(), unknown.data)
        self.assertIn("MSSV hoặc mật khẩu không đúng.".encode(), rejected.data)
        self.assertNotIn("đã bị từ chối".encode(), rejected.data)

        local_client = self.app.test_client()
        old_token = self.csrf(local_client)
        success = local_client.post("/login", data={
            "mssv": "STU001",
            "password": "fixture-ui-test-password",
            "csrf_token": old_token,
            "next": "/profile",
        }, environ_base={"REMOTE_ADDR": "198.51.100.10"})
        self.assertEqual(success.status_code, 302)
        self.assertEqual(success.headers["Location"], "/profile")
        self.assertIn("HttpOnly", success.headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", success.headers["Set-Cookie"])
        if self.app.config.get("APP_ENV") == "production":
            self.assertIn("Secure", success.headers["Set-Cookie"])
        with local_client.session_transaction() as session:
            self.assertTrue(session["_permanent"])
            # session.clear() khi đăng nhập → token CSRF gốc cũ bị xóa (session mới)
            self.assertNotIn("csrf_token", session)

        g.pop("_login_user", None)
        external_client = self.app.test_client()
        external_token = self.csrf(external_client)
        external = external_client.post("/login", data={
            "mssv": "STU001",
            "password": "fixture-ui-test-password",
            "csrf_token": external_token,
            "next": "//attacker.example/path",
        }, environ_base={"REMOTE_ADDR": "198.51.100.11"})
        self.assertEqual(external.headers["Location"], "/home")
        self.assertNotIn("attacker.example", external.headers["Location"])

        g.pop("_login_user", None)
        for _ in range(5):
            self.client.post("/login", data={
                "mssv": "STU001", "password": "wrong-password", "csrf_token": token,
            })
        locked = self.client.post("/login", data={
            "mssv": "STU001", "password": "fixture-ui-test-password", "csrf_token": token,
        })
        self.assertEqual(locked.status_code, 200)
        self.assertIn("đang tạm khóa".encode(), locked.data)

    def _fail(self, code="STU001", times=1, address="203.0.113.1"):
        g.pop("_login_user", None)
        client = self.app.test_client()
        token = self.csrf(client)
        for _ in range(times):
            client.post("/login", data={"mssv": code, "password": "wrong-password", "csrf_token": token},
                        environ_base={"REMOTE_ADDR": address})
        g.pop("_login_user", None)

    def test_lockout_is_stored_per_account_in_the_database(self):
        from datetime import timedelta
        from app.models import AuditLog
        from app.timeutil import utcnow

        self._fail(times=4, address="203.0.113.1")
        self._fail(times=1, address="203.0.113.2")          # đổi IP không thoát được bộ đếm
        user = db.session.get(User, 1)
        db.session.refresh(user)
        self.assertEqual(user.dang_nhap_sai, 5)
        first_lock = user.khoa_dang_nhap_den - utcnow()
        self.assertTrue(timedelta(minutes=4) < first_lock <= timedelta(minutes=5))
        self.assertEqual(AuditLog.query.filter_by(action="login_locked").count(), 1)

        locked = self.login(self.app.test_client())         # đúng mật khẩu, IP khác, vẫn bị khóa
        self.assertEqual(locked.status_code, 200)
        self.assertIn("Thử lại sau 5 phút".encode(), locked.data)
        db.session.refresh(user)
        self.assertEqual(user.dang_nhap_sai, 5)             # lúc khóa không đếm thêm

        user.khoa_dang_nhap_den = utcnow() - timedelta(seconds=1)   # hết hạn khóa
        db.session.commit()
        self._fail(times=5)
        db.session.refresh(user)
        self.assertTrue(timedelta(minutes=14) < user.khoa_dang_nhap_den - utcnow() <= timedelta(minutes=15))

        user.set_password("new-fixture-password")           # quên mật khẩu / mật khẩu tạm → mở khóa
        db.session.commit()
        self.assertEqual(self.login(self.app.test_client(), password="new-fixture-password").status_code, 302)

        self._fail(times=3)
        self.login(self.app.test_client(), password="new-fixture-password")
        db.session.refresh(user)
        self.assertEqual((user.dang_nhap_sai, user.khoa_dang_nhap_den), (0, None))   # đăng nhập đúng: xóa bộ đếm

        g.pop("_login_user", None)
        unknown = self.app.test_client()
        token = self.csrf(unknown)
        for _ in range(6):
            response = unknown.post("/login", data={"mssv": "UNKNOWN1", "password": "x-password",
                                                    "csrf_token": token})
        self.assertIn("MSSV hoặc mật khẩu không đúng.".encode(), response.data)

    def test_pending_login_redirects_to_pending_and_logout_is_post_only(self):
        client = self.app.test_client()
        response = self.login(client, code="STU002")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/pending")
        g.pop("_login_user", None)
        self.assertEqual(client.get("/login").headers["Location"], "/pending")
        g.pop("_login_user", None)
        pending = client.get("/pending")
        self.assertEqual(pending.status_code, 200)
        self.assertIn(b'name="csrf_token"', pending.data)
        self.assertEqual(client.get("/logout").status_code, 405)

        token = csrf_for(client)
        logout = client.post("/logout", data={"csrf_token": token})
        self.assertEqual(logout.status_code, 302)
        self.assertEqual(logout.headers["Location"], "/login")

    def test_registration_rejects_duplicate_and_malformed_ban_ids_without_database_errors(self):
        self.assertIn(b'name="csrf_token"', self.client.get("/register").data)
        token = self.csrf()
        form = {
            "csrf_token": token,
            "mssv": "SE240011",
            "ho_ten": "New Member",
            "ngay_sinh": "2003-01-01",
            "sdt": "0912345678",
            "email": "new.member@example.com",
            "facebook_link": "https://facebook.com/new.member",
            "ban_hoat_dong": ["1", "1"],
            "password": "new-account-password-2026",
            "confirm_password": "new-account-password-2026",
        }
        created = self.client.post("/register", data=form)
        self.assertEqual(created.status_code, 302)
        new_user = User.query.filter_by(mssv="SE240011").one()
        self.assertEqual(len(new_user.ban_links), 1)

        duplicate = self.client.post("/register", data=form)
        self.assertEqual(duplicate.status_code, 200)
        self.assertIn("đã được đăng ký".encode(), duplicate.data)
        # Đã lưu theo định dạng mới: SĐT +84 + 9 số, Facebook https://facebook.com/<id>
        self.assertEqual(new_user.sdt, "+84912345678")
        self.assertEqual(new_user.facebook_link, "https://facebook.com/new.member")

        malformed = {**form, "mssv": "SE240012", "ban_hoat_dong": ["999", "bad"]}
        response = self.client.post("/register", data=malformed)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Traceback".encode(), response.data)
        self.assertIsNone(User.query.filter_by(mssv="SE240012").first())

        too_long = {**form, "mssv": "SE240013", "password": "x1" * 65, "confirm_password": "x1" * 65}
        rejected = self.client.post("/register", data=too_long)
        self.assertEqual(rejected.status_code, 200)
        self.assertIsNone(User.query.filter_by(mssv="SE240013").first())

    def test_avatar_upload_requires_image_content_and_does_not_replace_with_invalid_data(self):
        response = self.login()
        self.assertEqual(response.status_code, 302)
        profile = self.client.get("/profile")
        avatar_form = profile.data.split(b'class="profile-avatar-form"', 1)[1].split(b"</form>", 1)[0]
        self.assertIn(b'name="csrf_token"', avatar_form)
        token = csrf_for(self.client)
        old_avatar = db.session.get(User, 1).avatar_url
        response = self.client.post("/profile/avatar", data={
            "csrf_token": token,
            "avatar": (io.BytesIO(b"not an image"), "avatar.png"),
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(db.session.get(User, 1).avatar_url, old_avatar)
        self.assertFalse(list((self.root / "avatars").glob("*")))

    def test_mail_stub_does_not_log_private_content_and_header_injection_is_rejected(self):
        with self.app.app_context():
            self.app.config.update({
                "MAIL_ENABLED": False,
                "MAIL_DEFAULT_SENDER": "sender@example.test",
            })
            with patch("builtins.print") as printed:
                self.assertFalse(send_email("member@example.test", "Private subject", "Private body"))
            printed.assert_not_called()

            self.app.config.update({
                "MAIL_ENABLED": True,
                "MAIL_USE_TLS": True,
                "MAIL_USE_SSL": False,
            })
            with patch("app.mail.smtplib.SMTP") as smtp:
                self.assertFalse(send_email(
                    "member@example.test",
                    "Hello\r\nBcc: attacker@example.test",
                    "Private body",
                ))
                smtp.assert_not_called()

    def test_sender_may_carry_a_display_name_but_not_injected_headers(self):
        self.app.config.update({"MAIL_ENABLED": True, "MAIL_USE_TLS": True, "MAIL_USE_SSL": False,
                                "MAIL_SERVER": "smtp.example.test", "MAIL_USERNAME": "fia@example.test",
                                "MAIL_PASSWORD": "app-password"})
        for sender, sent in (("fia@example.test", True), ("FIA Club <fia@example.test>", True),
                             ("FIA Club", False), ("FIA <fia@example.test>\r\nBcc: x@example.test", False)):
            with self.subTest(sender=sender), patch("app.mail.smtplib.SMTP") as smtp:
                self.app.config["MAIL_DEFAULT_SENDER"] = sender
                self.assertEqual(send_email("member@example.test", "Xin chào", "Nội dung"), sent)
                self.assertEqual(smtp.called, sent)
                if sent:
                    message = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
                    self.assertEqual(message["From"], sender)


if __name__ == "__main__":
    unittest.main()
