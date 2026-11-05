import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from flask import g

from tests.support import sign_csrf
from app.timeutil import club_now
from app import create_app, db
from app.models import Ban, Event, MemberRecord, Permission, Task, User, UserBan
from app.services.access import (
    can_manage_member,
    can_view_all_members,
    has_permission,
    is_board,
    is_treasurer,
    set_member_left_safely,
    set_member_role_safely,
    visible_members,
)
from app.permissions import can_assign_role


class TwoRoleSecurityTests(unittest.TestCase):
    """Kiểm thử chính sách phân quyền theo chức vụ (Role/Permission)."""
    ADMIN_ROLES = ((1, "CN"), (2, "PCN"), (3, "TB"), (4, "THUKY_THUQUY"))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        database_path = Path(self.directory.name) / "two-role-test.db"
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path}",
            "SECRET_KEY": "test-only-key",
            "AI_PROVIDER": "rules",
        })
        self.client = self.app.test_client()
        self.context = self.app.app_context()
        self.context.push()
        db.session.add_all([
            Ban(id=1, ten_ban="Engineering"),
            Ban(id=2, ten_ban="Communications"),
        ])
        for user_id, role in (*self.ADMIN_ROLES, (5, "THANH_VIEN"), (6, "THANH_VIEN")):
            user = User(
                id=user_id,
                mssv=f"SV{user_id:03d}",
                ho_ten=f"Member {user_id}",
                ngay_sinh=date(2003, 1, 1),
                sdt=f"09000000{user_id:02d}",
                email=f"member{user_id}@example.test",
                status="approved",
                chuc_vu=role,
            )
            user.set_password("test-password")
            db.session.add(user)
            db.session.add(MemberRecord(user_id=user_id, ngay_gia_nhap=date(2024, 1, 1)))
            db.session.add(UserBan(user_id=user_id, ban_id=1 if user_id % 2 else 2,
                                   is_truong_ban=(role == "TB")))
        db.session.commit()
        self.csrf_token = "two-role-test-csrf"

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.directory.cleanup()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
            session["csrf_token"] = self.csrf_token
        g.pop("_login_user", None)

    @property
    def csrf_headers(self):
        return {"X-CSRF-Token": sign_csrf(self.app, self.csrf_token)}

    # Bảng quyền đã thống nhất với CLB (xem app/models.py DEFAULT_ROLES):
    #                    CN  PCN  TB  TK/TQ  TV
    # Xem/duyệt TV       x   x    -   -      -
    # Quản lý TV         x   x    x   -      -
    # Sự kiện & task     x   x    x   x      -
    # Quỹ (ghi/duyệt)    x   x    -   x      -
    # Phân vai trò       x   x*   -   -      -   (* PCN không gán PCN/CN)
    # Nhường chức vụ     x   x    x   x      -
    MATRIX = {
        "CN": {"approve": True, "members": True, "events": True, "funds": True, "assign": True, "handoff": True},
        "PCN": {"approve": True, "members": True, "events": True, "funds": True, "assign": True, "handoff": True},
        "TB": {"approve": False, "members": True, "events": True, "funds": False, "assign": False, "handoff": True},
        "THUKY_THUQUY": {"approve": False, "members": False, "events": True, "funds": True, "assign": False, "handoff": True},
        "THANH_VIEN": {"approve": False, "members": False, "events": False, "funds": False, "assign": False, "handoff": False},
    }

    def test_permission_matrix_matches_club_policy(self):
        for user_id, role in (*self.ADMIN_ROLES, (5, "THANH_VIEN")):
            actor = db.session.get(User, user_id)
            expected = self.MATRIX[role]
            checks = {
                "approve": has_permission(actor, Permission.APPROVE_MEMBERS),
                "members": has_permission(actor, Permission.MANAGE_MEMBERS),
                "events": has_permission(actor, Permission.MANAGE_EVENTS),
                "funds": has_permission(actor, Permission.MANAGE_FUNDS),
                "assign": has_permission(actor, Permission.ASSIGN_ROLES),
                "handoff": has_permission(actor, Permission.HANDOFF_ROLE),
            }
            self.assertEqual(checks, expected, role)
            self.assertEqual(is_board(actor), role != "THANH_VIEN", role)
            self.assertEqual(is_treasurer(actor), expected["funds"], role)
            self.assertEqual(can_view_all_members(actor), expected["members"], role)
            self.assertEqual(can_manage_member(actor, db.session.get(User, 6)), expected["members"], role)
            expected_ids = set(range(1, 7)) if expected["members"] else {user_id}
            self.assertEqual({row.id for row in visible_members(actor)}, expected_ids, role)

            self.login(user_id)
            self.assertEqual(self.client.get("/admin/roles").status_code,
                             200 if expected["handoff"] else 403, role)
            self.assertEqual(self.client.get("/admin/approve").status_code,
                             200 if expected["approve"] else 403, role)
            response = self.client.get("/api/v1/members")
            self.assertEqual(response.status_code, 200)
            self.assertEqual({row["id"] for row in response.json["items"]}, expected_ids, role)
            self.assertEqual(self.client.get("/api/v1/reports/funds.xlsx").status_code,
                             200 if expected["funds"] else 403, role)

        self.assertFalse(can_assign_role(db.session.get(User, 1), db.session.get(User, 5), "CN", {})[0])

    def test_template_management_is_limited_to_cn_and_pcn(self):
        for user_id, role in self.ADMIN_ROLES:
            self.login(user_id)
            response = self.client.post("/kho-mau/email", data={
                "name": f"Mau {user_id}", "subject": "Tieu de", "body": "Noi dung"},
                headers=self.csrf_headers)
            self.assertEqual(response.status_code, 302 if role in {"CN", "PCN"} else 403, role)

    def test_member_listing_and_member_ids_stay_in_own_scope(self):
        self.login(5)
        response = self.client.get("/api/v1/members")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.json["items"]], [5])
        self.assertEqual(self.client.get("/api/v1/members/6").status_code, 403)
        self.assertEqual(self.client.patch("/api/v1/members/6", json={"name": "Changed"},
                                           headers=self.csrf_headers).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/members/6/archive",
                                          headers=self.csrf_headers).status_code, 403)
        self.assertEqual(self.client.patch("/api/v1/members/5", json={"status": "left"},
                                           headers=self.csrf_headers).status_code, 403)

    def test_role_assignment_follows_rank_and_cn_uses_the_transfer_workflow(self):
        # Thư ký/Thủ quỹ và Trưởng ban không có quyền phân vai trò
        for user_id in (3, 4):
            self.login(user_id)
            denied = self.client.post("/admin/roles/5/assign", data={"chuc_vu": "THUKY_THUQUY"},
                                      headers=self.csrf_headers)
            self.assertEqual(denied.status_code, 403)
        self.assertEqual(db.session.get(User, 5).chuc_vu, "THANH_VIEN")

        # PCN không được gán PCN/CN và không đổi vai trò của CN/PCN khác
        self.login(2)
        for new_role in ("PCN", "CN"):
            refused = self.client.post("/admin/roles/5/assign", data={"chuc_vu": new_role},
                                       headers=self.csrf_headers)
            self.assertEqual(refused.status_code, 302)
            self.assertEqual(db.session.get(User, 5).chuc_vu, "THANH_VIEN")
        self.client.post("/admin/roles/1/assign", data={"chuc_vu": "THANH_VIEN"}, headers=self.csrf_headers)
        self.assertEqual(db.session.get(User, 1).chuc_vu, "CN")
        # ... nhưng gán được vai trò thấp hơn mình
        demoted = self.client.post("/admin/roles/4/assign", data={"chuc_vu": "THANH_VIEN"},
                                   headers=self.csrf_headers)
        self.assertEqual(demoted.status_code, 302)
        self.assertEqual(db.session.get(User, 4).chuc_vu, "THANH_VIEN")

        # CN gán được PCN; CN chỉ chuyển qua quy trình nhường quyền
        self.login(1)
        promoted = self.client.post("/admin/roles/6/assign", data={"chuc_vu": "THUKY_THUQUY"},
                                    headers=self.csrf_headers)
        self.assertEqual(promoted.status_code, 302)
        self.assertEqual(db.session.get(User, 6).chuc_vu, "THUKY_THUQUY")
        direct_cn = self.client.post("/admin/roles/5/assign", data={"chuc_vu": "CN"},
                                     headers=self.csrf_headers)
        self.assertEqual(direct_cn.status_code, 302)
        self.assertEqual(db.session.get(User, 5).chuc_vu, "THANH_VIEN")

        handoff = self.client.post("/admin/roles/nhuong-quyen",
                                   data={"target_id": "5"},
                                   headers=self.csrf_headers)
        self.assertEqual(handoff.status_code, 302)
        self.assertEqual(db.session.get(User, 1).chuc_vu, "THANH_VIEN")
        self.assertEqual(db.session.get(User, 5).chuc_vu, "CN")
        self.assertEqual(User.query.filter_by(status="approved", chuc_vu="CN").count(), 1)

    def test_every_board_role_can_hand_off_its_own_designation(self):
        self.login(4)
        handoff = self.client.post("/admin/roles/nhuong-quyen", data={"target_id": "6"},
                                   headers=self.csrf_headers)
        self.assertEqual(handoff.status_code, 302)
        self.assertEqual(db.session.get(User, 4).chuc_vu, "THANH_VIEN")
        self.assertEqual(db.session.get(User, 6).chuc_vu, "THUKY_THUQUY")

    def test_last_active_admin_cannot_be_archived_left_or_downgraded(self):
        for user_id in (2, 3, 4):
            db.session.get(User, user_id).chuc_vu = "THANH_VIEN"
        db.session.commit()
        last_admin = db.session.get(User, 1)
        record = db.session.get(MemberRecord, 1)

        self.assertFalse(set_member_role_safely(last_admin, "THANH_VIEN"))
        db.session.rollback()
        last_admin = db.session.get(User, 1)
        record = db.session.get(MemberRecord, 1)
        self.assertFalse(set_member_left_safely(last_admin, record))
        db.session.rollback()
        self.assertFalse(set_member_left_safely(last_admin, record, club_now()))
        db.session.rollback()
        self.assertEqual(db.session.get(User, 1).chuc_vu, "CN")
        self.assertEqual(db.session.get(MemberRecord, 1).tinh_trang, "active")

        self.login(1)
        self.assertEqual(self.client.patch("/api/v1/members/1", json={"status": "left"},
                                           headers=self.csrf_headers).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/members/1/archive",
                                          headers=self.csrf_headers).status_code, 403)

    def test_cancelled_and_ended_events_reject_every_task_mutation(self):
        for index, status in enumerate(("cancelled", "da_ket_thuc"), start=1):
            event_id = 20 + index
            task_id = 30 + index
            start = club_now() - timedelta(days=1)
            event = Event(
                id=event_id, ten_su_kien=f"Terminal {status}", ma_su_kien=f"TERM{index}",
                thoi_gian_bat_dau=start, thoi_gian_ket_thuc=start + timedelta(hours=2),
                trang_thai=status, tao_boi_id=1,
            )
            db.session.add(event)
            db.session.add(Task(
                id=task_id, event_id=event_id, ten_task="Terminal task", deadline=start,
                assignee_id=5, ban_id=1, trang_thai="dang_lam",
            ))
        db.session.commit()

        for event_id, task_id in ((21, 31), (22, 32)):
            self.login(3)
            self.assertEqual(self.client.post(f"/event/{event_id}/task/new", headers=self.csrf_headers).status_code, 400)
            self.assertEqual(self.client.post(f"/task/{task_id}/chi-dinh-lai", headers=self.csrf_headers).status_code, 400)
            self.assertEqual(self.client.post(f"/task/{task_id}/cap-nhat-trang-thai",
                                              data={"trang_thai": "huy"},
                                              headers=self.csrf_headers).status_code, 400)
            self.login(5)
            self.assertEqual(self.client.post(f"/task/{task_id}/nop-san-pham",
                                              data={"noi_dung_nop": "submission"},
                                              headers=self.csrf_headers).status_code, 400)

    def test_html_api_and_ctftime_event_creation_are_available_to_all_admin_roles(self):
        starts = club_now() + timedelta(days=2)
        ends = starts + timedelta(hours=3)
        for user_id, _role in self.ADMIN_ROLES:
            self.login(user_id)
            html_response = self.client.post("/event/new", data={
                "ma_su_kien": f"HTML{user_id}",
                "ten_su_kien": f"HTML event {user_id}",
                "thoi_gian_bat_dau": starts.strftime("%Y-%m-%dT%H:%M"),
                "thoi_gian_ket_thuc": ends.strftime("%Y-%m-%dT%H:%M"),
            }, headers=self.csrf_headers)
            self.assertEqual(html_response.status_code, 302)
            self.assertIsNotNone(Event.query.filter_by(ma_su_kien=f"HTML{user_id}").first())

            api_response = self.client.post("/api/v1/events", json={
                "name": f"API event {user_id}", "code": f"API{user_id}",
                "start_at": starts.isoformat(), "end_at": ends.isoformat(),
            }, headers=self.csrf_headers)
            self.assertEqual(api_response.status_code, 201, api_response.json)

            source_id = str(1000 + user_id)
            source_event = {
                "start": starts.isoformat(), "finish": ends.isoformat(),
                "title": f"CTF event {user_id}", "location": "Online",
            }
            with patch("app.routes.event.event_by_id",
                       return_value=(source_event, {"stale": False})):
                ctf_response = self.client.post("/event/import/ctftime",
                                                data={"event_id": source_id},
                                                headers=self.csrf_headers)
            self.assertEqual(ctf_response.status_code, 302)
            self.assertIsNotNone(Event.query.filter_by(ma_su_kien=f"CTF-{source_id}").first())


if __name__ == "__main__":
    unittest.main()
