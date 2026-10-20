"""Kiểm thử các chức năng xác thực bổ sung (Ch6), ProxyFix/Flask-Limiter (Ch9-10),
migration (Ch5) và job định kỳ (Ch11)."""
import re
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from flask import g, request

from tests.support import csrf_for
from app import create_app, db
from app.models import Event, FundPeriod, MemberRecord, Notification, Role, Task, User
from app.validators import password_problems

PASSWORD = "Matkhau-2026"


class AccountSecurityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.directory.name) / "account.db"
        self.app = self.make_app()
        self.client = self.app.test_client()
        self.context = self.app.app_context()
        self.context.push()
        for user_id, role, status in ((1, "CN", "approved"), (2, "TB", "approved"),
                                      (3, "THANH_VIEN", "approved"), (4, "THUKY_THUQUY", "approved")):
            user = User(id=user_id, mssv=f"SV{user_id:04d}", ho_ten=f"User {user_id}",
                        ngay_sinh=date(2003, 1, 1), sdt="0912345678",
                        email=f"user{user_id}@example.com", status=status, chuc_vu=role)
            user.password = PASSWORD
            db.session.add(user)
            db.session.add(MemberRecord(user_id=user_id, ngay_gia_nhap=date(2024, 1, 1)))
        db.session.commit()

    def make_app(self, **extra):
        config = {"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{self.db_path}",
                  "SECRET_KEY": "account-test-secret-key-0123456789abcdef",
                  "AI_PROVIDER": "rules", "SCHEDULER_ENABLED": False}
        config.update(extra)
        return create_app(config)

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.directory.cleanup()

    def login(self, client, mssv, password=PASSWORD, **extra):
        g.pop("_login_user", None)
        response = client.post("/login", data={"csrf_token": csrf_for(client), "mssv": mssv,
                                               "password": password, **extra})
        g.pop("_login_user", None)
        return response

    def post(self, client, path, data=None):
        g.pop("_login_user", None)
        response = client.post(path, data={"csrf_token": csrf_for(client), **(data or {})})
        g.pop("_login_user", None)
        return response

    @staticmethod
    def link_from(mail_mock, prefix):
        body = mail_mock.call_args.args[2]
        match = re.search(r"http://localhost(/" + prefix + r"/\S+)", body)
        return match.group(1)

    # ---------------- chính sách mật khẩu ----------------
    def test_password_policy(self):
        self.assertTrue(password_problems("short1"))
        self.assertTrue(password_problems("chicochu"))
        self.assertTrue(password_problems("1234567890"))
        self.assertTrue(password_problems("password123"))
        self.assertTrue(password_problems("SV0001abc9", mssv="SV0001"))
        self.assertEqual(password_problems("Matkhau-2026", mssv="SV0001"), [])

    # ---------------- xác minh email ----------------
    def test_registration_sends_confirmation_link_that_verifies_email_once(self):
        client = self.app.test_client()
        with patch("app.routes.auth.send_email", return_value=True) as mail:
            response = self.post(client, "/register", {
                "mssv": "SE240001", "ho_ten": "New Member", "ngay_sinh": "2004-05-06",
                "sdt": "0912345678", "email": "New.Member@Example.com",
                "facebook_link": "https://facebook.com/new", "ban_hoat_dong": [],
                "password": PASSWORD, "confirm_password": PASSWORD})
        # chưa có ban nào trong DB → form báo lỗi, không tạo tài khoản
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(User.query.filter_by(mssv="SE240001").first())
        mail.assert_not_called()

        from app.models import Ban
        db.session.add(Ban(id=1, ten_ban="Kỹ thuật"))
        db.session.commit()
        mismatch = self.post(client, "/register", {
            "mssv": "SE240001", "ho_ten": "New Member", "ngay_sinh": "2004-05-06",
            "sdt": "0912345678", "email": "new.member@example.com",
            "facebook_link": "https://facebook.com/new", "ban_hoat_dong": ["1"],
            "password": PASSWORD, "confirm_password": PASSWORD + "x"})
        self.assertIn("Mật khẩu nhập lại không khớp.".encode(), mismatch.data)
        duplicate_email = self.post(client, "/register", {
            "mssv": "SE240002", "ho_ten": "Dup", "ngay_sinh": "2004-05-06",
            "sdt": "0912345678", "email": "USER1@example.com",
            "facebook_link": "https://facebook.com/dup", "ban_hoat_dong": ["1"],
            "password": PASSWORD, "confirm_password": PASSWORD})
        self.assertIn("Email này đã được dùng".encode(), duplicate_email.data)

        with patch("app.routes.auth.send_email", return_value=True) as mail:
            created = self.post(client, "/register", {
                "mssv": "SE240001", "ho_ten": "New Member", "ngay_sinh": "2004-05-06",
                "sdt": "0912345678", "email": "New.Member@Example.com",
                "facebook_link": "https://facebook.com/new", "ban_hoat_dong": ["1"],
                "password": PASSWORD, "confirm_password": PASSWORD})
        self.assertEqual(created.status_code, 302)
        user = User.query.filter_by(mssv="SE240001").one()
        self.assertEqual(user.email, "new.member@example.com")
        self.assertFalse(user.email_confirmed)
        link = self.link_from(mail, "confirm")

        tampered = client.get(link[:-3] + "abc")
        self.assertEqual(tampered.status_code, 302)
        db.session.expire_all()
        self.assertFalse(db.session.get(User, user.id).email_confirmed)

        confirmed = client.get(link)
        self.assertEqual(confirmed.status_code, 302)
        db.session.expire_all()
        self.assertTrue(db.session.get(User, user.id).email_confirmed)

        # Token xác minh không dùng được cho đặt lại mật khẩu (salt khác nhau)
        token = link.rsplit("/", 1)[1]
        self.assertEqual(client.get(f"/password/reset/{token}").status_code, 302)
        self.assertIsNone(User.from_token(token, "reset", 3600))

    # ---------------- quên / đặt lại mật khẩu ----------------
    def test_forgot_password_is_generic_and_reset_token_is_single_use(self):
        client = self.app.test_client()
        with patch("app.routes.auth.send_email", return_value=True) as mail:
            unknown = self.post(client, "/password/forgot", {"email": "nobody@example.com"})
            mail.assert_not_called()
            known = self.post(client, "/password/forgot", {"email": "user3@example.com"})
        self.assertEqual(unknown.status_code, 302)
        self.assertEqual(known.status_code, 302)
        link = self.link_from(mail, "password/reset")

        weak = self.post(client, link, {"new_password": "abc", "confirm_password": "abc"})
        self.assertEqual(weak.status_code, 200)
        self.assertIn("Mật khẩu phải có từ 8".encode(), weak.data)

        new_password = "MoiHon-2027"
        done = self.post(client, link, {"new_password": new_password, "confirm_password": new_password})
        self.assertEqual(done.status_code, 302)
        db.session.expire_all()
        self.assertTrue(db.session.get(User, 3).check_password(new_password))
        self.assertTrue(db.session.get(User, 3).email_confirmed)

        reused = self.post(client, link, {"new_password": "KhacNua-2028", "confirm_password": "KhacNua-2028"})
        self.assertEqual(reused.status_code, 302)
        self.assertIn("/password/forgot", reused.headers["Location"])
        db.session.expire_all()
        self.assertTrue(db.session.get(User, 3).check_password(new_password))
        self.assertEqual(self.login(self.app.test_client(), "SV0003", new_password).status_code, 302)

    # ---------------- đổi mật khẩu ----------------
    def test_change_password_validates_and_invalidates_other_sessions(self):
        first, second = self.app.test_client(), self.app.test_client()
        self.assertEqual(self.login(first, "SV0003").headers["Location"], "/home")
        self.assertEqual(self.login(second, "SV0003").headers["Location"], "/home")

        wrong = self.post(first, "/password/change", {"current_password": "sai-mat-khau1",
                                                      "new_password": "MoiHon-2027",
                                                      "confirm_password": "MoiHon-2027"})
        self.assertIn("Mật khẩu hiện tại không đúng.".encode(), wrong.data)
        changed = self.post(first, "/password/change", {"current_password": PASSWORD,
                                                        "new_password": "MoiHon-2027",
                                                        "confirm_password": "MoiHon-2027"})
        self.assertEqual(changed.status_code, 302)

        g.pop("_login_user", None)
        self.assertEqual(first.get("/home").status_code, 200)        # phiên hiện tại vẫn dùng được
        g.pop("_login_user", None)
        self.assertEqual(second.get("/home").status_code, 302)       # phiên cũ ở thiết bị khác bị đăng xuất

    # ---------------- fresh login / xác nhận lại mật khẩu ----------------
    def test_sensitive_actions_require_fresh_login(self):
        client = self.app.test_client()
        self.login(client, "SV0001")
        with client.session_transaction() as session:
            session["_fresh"] = False          # như phiên được khôi phục từ cookie "ghi nhớ"
        stale = self.post(client, "/admin/roles/3/assign", {"chuc_vu": "TB", "ban_id": "1"})
        self.assertEqual(stale.status_code, 302)
        self.assertIn("/reauth", stale.headers["Location"])
        self.assertEqual(db.session.get(User, 3).chuc_vu, "THANH_VIEN")

        bad = self.post(client, "/reauth", {"password": "sai-mat-khau1"})
        self.assertIn("Mật khẩu không đúng.".encode(), bad.data)
        good = self.post(client, "/reauth?next=/admin/roles", {"password": PASSWORD, "next": "/admin/roles"})
        self.assertEqual(good.headers["Location"], "/admin/roles")
        with client.session_transaction() as session:
            self.assertTrue(session["_fresh"])

    def test_remember_me_sets_httponly_cookie(self):
        client = self.app.test_client()
        response = self.login(client, "SV0003", remember="y")
        cookies = response.headers.getlist("Set-Cookie")
        remember = [cookie for cookie in cookies if cookie.startswith("fia_remember=")]
        self.assertEqual(len(remember), 1)
        self.assertIn("HttpOnly", remember[0])
        self.assertIn("SameSite=Lax", remember[0])

    # ---------------- mật khẩu tạm do BĐH cấp ----------------
    def test_temporary_password_forces_change_and_respects_rank(self):
        tb = self.app.test_client()
        self.login(tb, "SV0002")
        self.assertEqual(self.post(tb, "/admin/members/1/temp-password").status_code, 403)   # không chiếm tài khoản CN
        self.assertEqual(self.post(tb, "/admin/members/4/temp-password").status_code, 403)   # ngang cấp
        issued = self.post(tb, "/admin/members/3/temp-password")
        self.assertEqual(issued.status_code, 200)
        temporary = re.search(rb'<code class="temp-password">([^<]+)</code>', issued.data).group(1).decode()
        self.assertIn("no-store", issued.headers["Cache-Control"])

        tkq = self.app.test_client()
        self.login(tkq, "SV0004")
        self.assertEqual(self.post(tkq, "/admin/members/3/temp-password").status_code, 403)  # không có MANAGE_MEMBERS

        member = self.app.test_client()
        response = self.login(member, "SV0003", temporary)
        self.assertEqual(response.headers["Location"], "/password/change")
        g.pop("_login_user", None)
        self.assertEqual(member.get("/home").headers["Location"], "/password/change")
        g.pop("_login_user", None)
        self.assertEqual(member.get("/api/v1/members").status_code, 403)
        changed = self.post(member, "/password/change", {"current_password": temporary,
                                                         "new_password": "MoiHon-2027",
                                                         "confirm_password": "MoiHon-2027"})
        self.assertEqual(changed.status_code, 302)
        g.pop("_login_user", None)
        self.assertEqual(member.get("/home").status_code, 200)

    # ---------------- Flask-Limiter ----------------
    def test_login_endpoint_is_rate_limited_per_ip(self):
        app = self.make_app(RATELIMIT_ENABLED=True, AUTH_RATE_LIMIT="2 per minute",
                            AUTO_CREATE_TABLES=False)
        client = app.test_client()
        with app.app_context():
            statuses = [client.post("/login", data={"csrf_token": csrf_for(client), "mssv": "SV0003",
                                                    "password": "sai-mat-khau1"}).status_code
                        for _ in range(3)]
        self.assertEqual(statuses[:2], [200, 200])
        self.assertEqual(statuses[2], 429)

    # ---------------- ProxyFix ----------------
    def test_proxy_headers_are_trusted_only_when_configured(self):
        for hops, expected in ((0, ("http", "127.0.0.1")), (1, ("https", "203.0.113.9"))):
            app = self.make_app(TRUST_PROXY_HOPS=hops, AUTO_CREATE_TABLES=False)
            app.add_url_rule("/_probe", "probe", lambda: f"{request.scheme}|{request.remote_addr}")
            response = app.test_client().get("/_probe", headers={
                "X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"})
            self.assertEqual(tuple(response.get_data(as_text=True).split("|")), expected)

    # ---------------- job định kỳ ----------------
    def test_scheduler_jobs_send_deadline_mail_and_notify_fund_managers(self):
        from app.scheduler import check_deadlines, check_period_reminder
        from app.services.common import club_now
        today = club_now()
        event = Event(id=1, ten_su_kien="Workshop", ma_su_kien="WS1", thoi_gian_bat_dau=today,
                      thoi_gian_ket_thuc=today + timedelta(days=10), tao_boi_id=1)
        db.session.add(event)
        db.session.add(Task(event_id=1, ten_task="Slide", deadline=today + timedelta(days=7),
                            assignee_id=3, trang_thai="dang_lam"))
        db.session.add(FundPeriod(ten_ky="Fall 2026", ngay_bat_dau=today.date() - timedelta(days=60),
                                  ngay_ket_thuc=today.date() + timedelta(days=5), is_current=True))
        db.session.commit()
        with patch("app.scheduler.send_email", return_value=True) as mail:
            self.assertEqual(check_deadlines(self.app), 1)
            self.assertEqual(mail.call_args.args[0], "user3@example.com")
            self.assertEqual(check_period_reminder(self.app), 2)       # CN + Thư ký/Thủ quỹ
            self.assertEqual(check_period_reminder(self.app), 0)       # không nhắc lặp lại
        notified = {row.user_id for row in Notification.query.all()}
        self.assertEqual(notified, {1, 3, 4})  # quỹ: CN/TK; nhắc deadline trong web: người nhận task


class MigrationTests(unittest.TestCase):
    """Schema tạo bằng Flask-Migrate phải giống hệt models và nạp sẵn bảng role."""

    def test_upgrade_creates_schema_and_roles_then_downgrades_cleanly(self):
        from flask_migrate import downgrade, upgrade
        from sqlalchemy import inspect
        with tempfile.TemporaryDirectory() as directory:
            app = create_app({"TESTING": True, "AUTO_CREATE_TABLES": False,
                              "SQLALCHEMY_DATABASE_URI": f"sqlite:///{directory}/m.db",
                              "SECRET_KEY": "migration-test-secret-key-0123456789", "SCHEDULER_ENABLED": False})
            with app.app_context():
                upgrade(directory=str(Path(__file__).resolve().parents[1] / "migrations"))
                tables = set(inspect(db.engine).get_table_names())
                self.assertTrue(set(db.metadata.tables) <= tables)
                self.assertEqual({role.name for role in Role.query},
                                 {"ADMIN", "CN", "PCN", "TB", "THUKY_THUQUY", "THANH_VIEN"})
                email_columns = {column["name"] for column in inspect(db.engine).get_columns("email_template")}
                self.assertTrue({"system_key", "keywords", "description"} <= email_columns)
                user_columns = {column["name"] for column in inspect(db.engine).get_columns("user")}
                self.assertTrue({"email_confirmed", "must_change_password", "password_changed_at"} <= user_columns)
                downgrade(directory=str(Path(__file__).resolve().parents[1] / "migrations"), revision="base")
                self.assertNotIn("user", set(inspect(db.engine).get_table_names()))
                db.engine.dispose()


if __name__ == "__main__":
    unittest.main()
