"""Định dạng đăng ký (MSSV, SĐT, Facebook, mật khẩu), tài khoản Quản trị hệ thống và kho mẫu."""
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from flask import g

from tests.support import csrf_for
from app import create_app, db
from app.models import (
    Ban, EmailProposal, EmailTemplate, Event, EventDetail, MemberRecord, ProposalTemplate, Role, Task,
    User, UserBan,
)
from app.services.assistant import RulesProvider
from app.services.common import club_now
from app.services.email_templates import render_system_email, seed_system_templates
from app.services.proposal_templates import seed_proposal_templates
from app.validators import (
    MSSV_RE, PASSWORD_MAX, normalize_facebook, normalize_mssv, normalize_phone, password_problems,
)

PASSWORD = "Matkhau-2026"


class FormatTests(unittest.TestCase):
    def test_mssv_format(self):
        for code in ("SE201256", "HA190001", "DS220123", "QE180000", "CA210999"):
            self.assertTrue(MSSV_RE.fullmatch(code), code)
        for code in ("NE201256", "SB201256", "SE20125", "SE2012567", "se201256", "SE20125A", "22110018"):
            self.assertIsNone(MSSV_RE.fullmatch(code), code)
        self.assertEqual(normalize_mssv(" se201256 "), "SE201256")

    def test_phone_is_plus84_and_nine_digits(self):
        for raw in ("912345678", "912 345 678", "+84912345678", "84912345678", "0912345678", "0912.345.678"):
            self.assertEqual(normalize_phone(raw), "+84912345678", raw)
        for raw in ("12345678", "0123", "091234567890", "012345678", "+8491234567", "abc", ""):
            self.assertIsNone(normalize_phone(raw), raw)

    def test_facebook_only_accepts_facebook_com_user_id(self):
        self.assertEqual(normalize_facebook("ten.cua.ban"), "https://facebook.com/ten.cua.ban")
        self.assertEqual(normalize_facebook("https://facebook.com/100012345"), "https://facebook.com/100012345")
        for raw in ("http://facebook.com/abc", "https://www.facebook.com/abc", "https://m.facebook.com/abc",
                    "https://fb.com/abc", "https://facebook.com/profile.php?id=1", "https://facebook.com/abc/",
                    "https://facebook.com/a/b", "ten ban", "javascript:alert(1)", ""):
            self.assertIsNone(normalize_facebook(raw), raw)

    def test_password_max_64(self):
        self.assertEqual(PASSWORD_MAX, 64)
        self.assertFalse(password_problems("a1" * 32))
        self.assertTrue(password_problems("a1" * 32 + "x"))


class _AppCase(unittest.TestCase):
    """App với 1 ban, Quản trị hệ thống (id 1), CN (2), TV (3), kho mẫu mặc định."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "format-admin-test-secret-0123456789abcdef",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(self.directory.name) / 't.db'}",
            "AI_PROVIDER": "rules", "SCHEDULER_ENABLED": False})
        self.client = self.app.test_client()
        self.context = self.app.app_context()
        self.context.push()
        Role.insert_roles()
        db.session.add_all([Ban(id=1, ten_ban="Kỹ thuật"), Ban(id=2, ten_ban="Truyền thông")])
        rows = ((1, "admin", "ADMIN", None), (2, "SE180001", "CN", 1), (3, "SE180003", "THANH_VIEN", 1))
        for user_id, code, role, ban in rows:
            user = User(id=user_id, mssv=code, ho_ten=f"User {user_id}", ngay_sinh=date(2003, 1, 1),
                        sdt="+84912345678", email=f"u{user_id}@example.com", status="approved",
                        chuc_vu=role)
            user.password = PASSWORD
            db.session.add(user)
            if ban:
                db.session.add(MemberRecord(user_id=user_id, ngay_gia_nhap=date(2024, 1, 1)))
                db.session.add(UserBan(user_id=user_id, ban_id=ban))
        db.session.commit()
        seed_system_templates()
        seed_proposal_templates()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.directory.cleanup()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = db.session.get(User, user_id).get_id()
            session["_fresh"] = True
        g.pop("_login_user", None)

    def post(self, path, data=None):
        g.pop("_login_user", None)
        response = self.client.post(path, data={"csrf_token": csrf_for(self.client), **(data or {})})
        g.pop("_login_user", None)
        return response

    def get(self, path):
        g.pop("_login_user", None)
        return self.client.get(path)


class RegistrationFormatTests(_AppCase):
    def form(self, **changes):
        base = {"mssv": "se240001", "ho_ten": "Nguyen Van A", "ngay_sinh": "2005-01-01",
                "sdt": "912345678", "email": "a@example.com", "facebook_link": "nguyen.van.a",
                "ban_hoat_dong": "2", "password": PASSWORD, "confirm_password": PASSWORD}
        return {**base, **changes}

    def test_register_normalizes_and_stores_single_ban(self):
        page = self.get("/register").get_data(as_text=True)
        self.assertIn('type="radio"', page)
        self.assertIn("+84", page)
        with patch("app.routes.auth.send_email", return_value=True):
            response = self.post("/register", self.form())
        self.assertEqual(response.status_code, 302)
        user = User.query.filter_by(mssv="SE240001").one()
        self.assertEqual((user.sdt, user.facebook_link), ("+84912345678", "https://facebook.com/nguyen.van.a"))
        self.assertEqual([link.ban_id for link in user.ban_links], [2])

    def test_register_rejects_bad_formats(self):
        cases = [({"mssv": "NE240001"}, "MSSV không đúng định dạng"),
                 ({"sdt": "12345"}, "Nhập 9 số sau +84"),
                 ({"facebook_link": "https://www.facebook.com/abc"}, "https://facebook.com/userID"),
                 ({"ban_hoat_dong": ""}, "Vui lòng chọn một ban"),
                 ({"password": "a1" * 33, "confirm_password": "a1" * 33}, "8 đến 64")]
        for change, message in cases:
            with self.subTest(message=message):
                response = self.post("/register", self.form(**change))
                self.assertEqual(response.status_code, 200)
                self.assertIn(message, response.get_data(as_text=True))
        self.assertEqual(User.query.count(), 3)


class AdminAccountTests(_AppCase):
    def test_admin_logs_in_with_username_and_is_not_a_member(self):
        g.pop("_login_user", None)
        token = csrf_for(self.client)
        response = self.client.post("/login", data={"csrf_token": token, "mssv": "admin", "password": PASSWORD})
        self.assertEqual(response.status_code, 302)
        g.pop("_login_user", None)
        for path in ("/home", "/admin/approve", "/admin/roles", "/kho-mau", "/funds", "/event"):
            self.assertEqual(self.get(path).status_code, 200, path)
        members = self.get("/member").get_data(as_text=True)
        self.assertNotIn(">admin<", members)
        self.assertNotIn("admin@admin", members)
        api = self.get("/api/v1/members").get_json()
        self.assertNotIn(1, [row["id"] for row in api["items"]])

    def test_lowercase_mssv_login(self):
        token = csrf_for(self.client)
        response = self.client.post("/login", data={"csrf_token": token, "mssv": "se180003", "password": PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("/login", response.headers["Location"])

    def test_only_admin_assigns_chu_nhiem_and_admin_role_is_protected(self):
        self.login(2)   # Chủ nhiệm không gán được Chủ nhiệm
        self.post("/admin/roles/3/assign", {"chuc_vu": "CN"})
        self.assertEqual(db.session.get(User, 3).chuc_vu, "THANH_VIEN")
        self.post("/admin/roles/1/assign", {"chuc_vu": "THANH_VIEN"})
        self.assertEqual(db.session.get(User, 1).chuc_vu, "ADMIN")
        db.session.get(User, 2).chuc_vu = "THANH_VIEN"
        db.session.commit()
        self.login(1)   # Quản trị hệ thống gán Chủ nhiệm đầu tiên
        response = self.post("/admin/roles/3/assign", {"chuc_vu": "CN"})
        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertEqual(db.session.get(User, 3).chuc_vu, "CN")
        self.post("/admin/roles/2/assign", {"chuc_vu": "ADMIN"})
        db.session.expire_all()
        self.assertEqual(db.session.get(User, 2).chuc_vu, "THANH_VIEN")


class TemplateStoreTests(_AppCase):
    def test_system_email_template_is_editable_and_validated(self):
        template = EmailTemplate.query.filter_by(system_key="member_approved").one()
        self.login(2)
        bad = self.post(f"/kho-mau/email/{template.id}", {"subject": "Chào {{link}}", "body": "x"})
        self.assertIn("Chỉ dùng các biến", bad.get_data(as_text=True) if bad.status_code == 200 else
                      self.get("/kho-mau").get_data(as_text=True))
        self.post(f"/kho-mau/email/{template.id}", {"subject": "[FIA] Chào mừng {{ten}}",
                                                    "body": "Xin chào {{ten}} ({{mssv}})"})
        self.assertEqual(render_system_email("member_approved", ten="An", mssv="SE1")[0], "[FIA] Chào mừng An")
        self.post(f"/kho-mau/email/{template.id}/reset")
        self.assertIn("thành viên chính thức", render_system_email("member_approved", ten="An", mssv="SE1")[0])
        self.login(3)
        self.assertEqual(self.get("/kho-mau").status_code, 403)

    def test_ai_email_draft_uses_store_then_general_template(self):
        self.login(2)
        self.post("/kho-mau/email", {"name": "Nhắc đóng quỹ", "keywords": "dong quy",
                                     "subject": "[CLB] Nhắc đóng quỹ cho {{ten}}",
                                     "body": "Chào {{ten}},\n{{noi_dung}}\n-- Thủ quỹ"})
        from app.services.email_proposals import maybe_create_email_draft
        actor = db.session.get(User, 2)
        stored = maybe_create_email_draft(actor, "gửi mail cho SE180003: nhắc đóng quỹ kỳ này", RulesProvider())
        self.assertIn("mẫu 'Nhắc đóng quỹ'", stored["answer"])
        by_keyword = maybe_create_email_draft(actor, "gửi mail cho SE180003: bạn còn thiếu dong quy", RulesProvider())
        self.assertIn("mẫu 'Nhắc đóng quỹ'", by_keyword["answer"])
        general = maybe_create_email_draft(actor, "gửi mail cho SE180003: mời họp ban tối thứ 5", RulesProvider())
        self.assertIn("theo mẫu chung", general["answer"])
        drafts = EmailProposal.query.order_by(EmailProposal.id).all()
        self.assertEqual(drafts[0].subject, "[CLB] Nhắc đóng quỹ cho User 3")
        self.assertIn("bạn còn thiếu dong quy", drafts[1].body)
        self.assertIn("mời họp ban tối thứ 5", drafts[2].body)
        self.assertEqual({draft.status for draft in drafts}, {"pending"})

    def test_similar_event_is_used_when_store_has_no_match(self):
        from app.services.proposal_templates import find_proposal_template
        now = club_now()
        old = Event(ten_su_kien="Ngày hội tân sinh viên 2025", ma_su_kien="NH25", trang_thai="da_ket_thuc",
                    thoi_gian_bat_dau=now - timedelta(days=300), thoi_gian_ket_thuc=now - timedelta(days=299),
                    tao_boi_id=2)
        db.session.add(old)
        db.session.flush()
        db.session.add(Task(event_id=old.id, ten_task="Dựng booth", deadline=now - timedelta(days=301),
                            ban_id=1, assignee_id=3))
        db.session.commit()
        found = find_proposal_template("task", "chia việc cho ngày hội tân sinh viên 2026")
        self.assertEqual(found["source"], "similar_event")
        self.assertEqual(found["content"]["tasks"][0]["name"], "Dựng booth")
        fallback = find_proposal_template("task", "việc gì đó không liên quan")
        self.assertEqual(fallback["source"], "default")

    def test_proposal_template_crud_requires_event_permission(self):
        self.login(2)
        self.post("/kho-mau/task", {"name": "Task talkshow", "keywords": "talkshow",
                                    "tasks": "Mời khách | liên hệ diễn giả\nTruyền thông", "is_default": "0"})
        template = ProposalTemplate.query.filter_by(name="Task talkshow").one()
        self.assertEqual(template.content["tasks"][1], {"name": "Truyền thông", "description": ""})
        self.post(f"/kho-mau/task/{template.id}/active", {"active": "no"})
        self.assertFalse(db.session.get(ProposalTemplate, template.id).active)
        self.login(3)
        self.assertEqual(self.post("/kho-mau/task", {"name": "X", "tasks": "a"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
