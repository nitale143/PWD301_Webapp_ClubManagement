"""BĐH (BOARD) và quản lý sự kiện (MANAGE_EVENTS) là hai quyền độc lập.

Hai vai trò thử nghiệm: SU_KIEN chỉ có MANAGE_EVENTS; BDH_THUAN chỉ có BOARD. Mỗi chức năng phải
kiểm đúng quyền của nó, kể cả giao diện, route, service và trợ lý AI.
"""

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from flask import g

from tests.support import csrf_for
from app import create_app, db
from app.models import Ban, Event, EventRegistration, MemberRecord, Permission, Role, User, UserBan
from app.services.access import board_role_names, can_manage_events, is_board
from app.services.club_data_tools import answer_by_rules
from app.services.common import DomainError, club_now

EVENT_MANAGER, BOARD_ONLY = 2, 3


class PermissionSplitTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "permission-split-secret-key-0123456789",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(self.directory.name) / 'split.db'}",
            "AI_PROVIDER": "rules", "SCHEDULER_ENABLED": False})
        self.client = self.app.test_client()
        self.context = self.app.test_request_context()
        self.context.push()
        Role.insert_roles()
        db.session.add_all([
            Role(name="SU_KIEN", label="Cộng tác viên sự kiện", rank=5, permissions=Permission.MANAGE_EVENTS),
            Role(name="BDH_THUAN", label="Cố vấn BĐH", rank=4, permissions=Permission.BOARD),
            Ban(id=1, ten_ban="Kỹ thuật"),
        ])
        for user_id, role in ((1, "CN"), (EVENT_MANAGER, "SU_KIEN"), (BOARD_ONLY, "BDH_THUAN"),
                              (4, "THANH_VIEN")):
            user = User(id=user_id, mssv=f"SE18000{user_id}", ho_ten=f"Member {user_id}",
                        ngay_sinh=date(2004, 1, 1), sdt=f"+8490000000{user_id}",
                        email=f"m{user_id}@example.com", status="approved", chuc_vu=role)
            user.set_password("test-password")
            db.session.add(user)
            db.session.add(MemberRecord(user_id=user_id, ngay_gia_nhap=date(2025, 1, 1)))
            db.session.add(UserBan(user_id=user_id, ban_id=1))
        now = club_now()
        self.event = Event(id=1, ten_su_kien="Workshop An toàn", ma_su_kien="WSAT",
                           thoi_gian_bat_dau=now + timedelta(days=2),
                           thoi_gian_ket_thuc=now + timedelta(days=2, hours=2),
                           trang_thai="sap_dien_ra", tao_boi_id=1)
        db.session.add(self.event)
        db.session.add(EventRegistration(event_id=1, user_id=4, trang_thai="registered"))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.directory.cleanup()

    def user(self, user_id):
        return db.session.get(User, user_id)

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
        g.pop("_login_user", None)

    def page(self, path):
        g.pop("_login_user", None)
        return self.client.get(path).get_data(as_text=True)

    def post(self, path, data=None):
        g.pop("_login_user", None)
        return self.client.post(path, data={"csrf_token": csrf_for(self.client), **(data or {})})

    def test_roles_have_exactly_one_of_the_two_permissions(self):
        self.assertTrue(can_manage_events(self.user(EVENT_MANAGER)))
        self.assertFalse(is_board(self.user(EVENT_MANAGER)))
        self.assertTrue(is_board(self.user(BOARD_ONLY)))
        self.assertFalse(can_manage_events(self.user(BOARD_ONLY)))
        # Bảo vệ "BĐH cuối cùng" đọc bảng role trong database, không phải danh sách viết cứng.
        self.assertIn("BDH_THUAN", board_role_names())
        self.assertNotIn("SU_KIEN", board_role_names())

    def test_event_manager_runs_events_but_not_board_tools(self):
        self.login(EVENT_MANAGER)
        detail = self.page("/event/1")
        for text in ("Bắt đầu ngay", "Giao nhiệm vụ mới", "Điểm danh sự kiện"):
            self.assertIn(text, detail)
        self.assertIn("Mở sự kiện để giao việc", self.page("/tasks"))
        ai = self.page("/ai")
        self.assertIn("Đề xuất sự kiện hoặc chia task", ai)
        self.assertIn("Đề xuất đang chờ duyệt", ai)
        self.assertNotIn("Tóm tắt hôm nay và báo cáo", ai)
        self.assertNotIn("Nháp email chờ BĐH duyệt", ai)
        self.assertEqual(self.post("/ai/reports/week/save").status_code, 403)
        self.assertNotEqual(self.post("/ai/proposals/new", {"kind": "event", "brief": "Workshop"}).status_code, 403)
        self.assertEqual(self.client.get("/kho-mau").status_code, 200)
        answer = answer_by_rules(self.user(EVENT_MANAGER), "bao nhiêu người đăng ký WSAT")["answer"]
        self.assertIn("Member 4", answer)

    def test_board_only_member_gets_reports_but_cannot_run_events(self):
        self.login(BOARD_ONLY)
        detail = self.page("/event/1")
        for text in ("Bắt đầu ngay", "Giao nhiệm vụ mới", "Điểm danh sự kiện"):
            self.assertNotIn(text, detail)
        self.assertNotIn("Mở sự kiện để giao việc", self.page("/tasks"))
        ai = self.page("/ai")
        self.assertIn("Tóm tắt hôm nay và báo cáo", ai)
        self.assertIn("Nháp email chờ BĐH duyệt", ai)
        self.assertNotIn("Đề xuất sự kiện hoặc chia task", ai)   # form không hiện khi gửi sẽ bị chặn
        self.assertEqual(self.post("/ai/proposals/new", {"kind": "event", "brief": "Workshop"}).status_code, 403)
        self.assertEqual(self.post("/event/1/trang-thai", {"trang_thai": "dang_dien_ra"}).status_code, 403)
        self.assertEqual(self.post("/ai/reports/week/save").status_code, 302)
        with self.assertRaises(DomainError):     # AI không rộng tay hơn trang sự kiện
            answer_by_rules(self.user(BOARD_ONLY), "bao nhiêu người đăng ký WSAT")

    def test_ordinary_member_sees_neither(self):
        self.login(4)
        ai = self.page("/ai")
        self.assertNotIn("Đề xuất sự kiện hoặc chia task", ai)
        self.assertNotIn("Tóm tắt hôm nay và báo cáo", ai)
        self.assertNotIn("Giao nhiệm vụ mới", self.page("/event/1"))


if __name__ == "__main__":
    unittest.main()
