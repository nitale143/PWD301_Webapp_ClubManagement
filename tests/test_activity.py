"""Tình trạng hoạt động: một nguồn duy nhất, điểm = 50% task + 50% điểm danh."""

import io
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from flask import g
from openpyxl import load_workbook

from tests.support import csrf_for
from app import create_app, db
from app.models import (
    Attendance, Ban, Event, EventDetail, EventRegistration, EventTargetBan, FundPeriod,
    MemberActivity, MemberRecord, Task, User, UserBan,
)
from app.services.activities import (
    combine, compute, label_of, level_filter, level_of, levels_for, refresh_period, score_to_level,
    summary,
)
from app.services.club_data_tools import answer_by_rules
from app.services.common import club_now


class ActivityFormulaTests(unittest.TestCase):
    def test_combine_uses_weights_and_falls_back_to_the_available_part(self):
        self.assertEqual(combine(100, 0), 50.0)
        self.assertEqual(combine(80, 60), 70.0)
        self.assertEqual(combine(None, 40), 40.0)     # không có task → chỉ điểm danh
        self.assertEqual(combine(90, None), 90.0)     # không có sự kiện phải tham gia → chỉ task
        self.assertIsNone(combine(None, None))

    def test_levels_follow_thresholds(self):
        self.assertEqual(score_to_level(None), "chua_xep_loai")
        self.assertEqual(score_to_level(0), "khong_hoat_dong")
        self.assertEqual(score_to_level(0.5), "it_hoat_dong")
        self.assertEqual(score_to_level(49.9), "it_hoat_dong")
        self.assertEqual(score_to_level(50), "hoat_dong")
        self.assertEqual(score_to_level(80), "tich_cuc")


class ActivityDataTests(unittest.TestCase):
    """Kỳ hiện tại có 2 sự kiện đã kết thúc (E1 mọi người, E2 chỉ ban Truyền thông) và 1 sự kiện
    đang diễn ra (không được tính)."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "activity-secret-key-0123456789abcdef",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(self.directory.name) / 'activity.db'}",
            "AI_PROVIDER": "rules", "SCHEDULER_ENABLED": False})
        self.client = self.app.test_client()
        self.context = self.app.test_request_context()
        self.context.push()
        now = club_now()
        self.period = FundPeriod(id=1, ten_ky="Fall 2026", ngay_bat_dau=(now - timedelta(days=60)).date(),
                                 ngay_ket_thuc=(now + timedelta(days=30)).date(), is_current=True)
        db.session.add_all([Ban(id=1, ten_ban="Kỹ thuật"), Ban(id=2, ten_ban="Truyền thông"), self.period])
        db.session.flush()
        # (id, mssv, chức vụ, ban)
        for user_id, role, ban in ((1, "CN", 1), (2, "THANH_VIEN", 1), (3, "THANH_VIEN", 2),
                                   (4, "THANH_VIEN", 1), (5, "THANH_VIEN", 1), (6, "THANH_VIEN", 2)):
            user = User(id=user_id, mssv=f"SE18000{user_id}", ho_ten=f"Member {user_id}",
                        ngay_sinh=date(2004, 1, 1), sdt=f"+8490000000{user_id}",
                        email=f"m{user_id}@example.com", status="approved", chuc_vu=role)
            user.set_password("test-password")
            db.session.add(user)
            db.session.add(MemberRecord(user_id=user_id, ngay_gia_nhap=date(2025, 1, 1)))
            db.session.add(UserBan(user_id=user_id, ban_id=ban))
        db.session.flush()
        db.session.get(User, 5).ky_tham_gia_id = 1                    # mới duyệt trong kỳ này
        db.session.get(MemberRecord, 6).tinh_trang = "paused"         # đang tạm nghỉ
        e1 = Event(id=1, ten_su_kien="Orientation", ma_su_kien="ORI", trang_thai="da_ket_thuc",
                   thoi_gian_bat_dau=now - timedelta(days=10, hours=2),
                   thoi_gian_ket_thuc=now - timedelta(days=10), tao_boi_id=1)
        e2 = Event(id=2, ten_su_kien="Media day", ma_su_kien="MEDIA", trang_thai="da_ket_thuc",
                   thoi_gian_bat_dau=now - timedelta(days=5, hours=2),
                   thoi_gian_ket_thuc=now - timedelta(days=5), tao_boi_id=1)
        e3 = Event(id=3, ten_su_kien="Đang chạy", ma_su_kien="LIVE", trang_thai="dang_dien_ra",
                   thoi_gian_bat_dau=now - timedelta(hours=1),
                   thoi_gian_ket_thuc=now + timedelta(hours=2), tao_boi_id=1)
        db.session.add_all([e1, e2, e3])
        db.session.add(EventDetail(event_id=2, tat_ca_thanh_vien=False))
        db.session.add(EventTargetBan(event_id=2, ban_id=2))
        db.session.flush()
        self._attend(1, 1, "on_time")
        self._attend(1, 2, "absent")
        self._attend(1, 3, "late")
        self._attend(2, 3, "on_time")
        self._attend(1, 4, "excused")
        self._attend(3, 2, "on_time")        # sự kiện chưa kết thúc: không tính
        deadline = now - timedelta(days=10, hours=1)
        for event_id, assignee, state in ((1, 2, "hoan_thanh"), (1, 2, "hoan_thanh"), (1, 3, "lam_lai"),
                                          (1, 3, "huy"), (3, 4, "dang_lam")):
            db.session.add(Task(event_id=event_id, ten_task="Việc", deadline=deadline, trang_thai=state,
                                assignee_id=assignee, ban_id=1))
        refresh_period(self.period)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.directory.cleanup()

    def _attend(self, event_id, user_id, state):
        registration = EventRegistration(event_id=event_id, user_id=user_id, trang_thai="registered")
        db.session.add(registration)
        db.session.flush()
        db.session.add(Attendance(registration_id=registration.id, trang_thai=state, checkin_boi_id=1))

    def user(self, user_id):
        return db.session.get(User, user_id)

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
        g.pop("_login_user", None)

    # ------------------------------------------------------------ tính toán
    def test_each_member_gets_one_consistent_row(self):
        expected = {
            # user: (task xong/tổng, có mặt/phải tham gia, điểm, mức)
            1: (0, 0, 1, 1, 100.0, "tich_cuc"),       # chỉ điểm danh, E2 không áp dụng ban 1
            2: (2, 2, 0, 1, 50.0, "hoat_dong"),       # 100% task + 0% điểm danh
            3: (0, 1, 2, 2, 50.0, "hoat_dong"),       # task hủy không tính; muộn vẫn là có mặt
            4: (0, 0, 0, 0, None, "chua_xep_loai"),   # có phép không tính; task của sự kiện đang chạy không tính
            5: (0, 0, 0, 0, None, "chua_xep_loai"),   # mới duyệt trong kỳ
        }
        for user_id, (done, total, present, events, score, level) in expected.items():
            row = MemberActivity.query.filter_by(user_id=user_id, period_id=1).one()
            self.assertEqual((row.task_hoan_thanh, row.tong_task, row.su_kien_co_mat, row.tong_su_kien,
                              row.diem, row.trang_thai), (done, total, present, events, score, level),
                             user_id)

    def test_paused_overrides_score_and_new_member_is_unrated(self):
        self.assertEqual(level_of(self.user(6)), "tam_nghi")
        self.assertEqual(label_of(self.user(6)), "Tạm nghỉ")
        self.assertEqual(level_of(self.user(5)), "chua_xep_loai")
        self.assertEqual(levels_for([self.user(1), self.user(6)]), {1: "tich_cuc", 6: "tam_nghi"})

    def test_member_without_any_stored_row_is_unrated(self):
        MemberActivity.query.filter_by(user_id=2).delete()
        db.session.commit()
        self.assertEqual(level_of(self.user(2)), "chua_xep_loai")
        unrated = User.query.filter(level_filter("chua_xep_loai")).order_by(User.id).all()
        self.assertEqual([user.id for user in unrated], [2, 4, 5])

    def test_sql_filter_matches_python_level(self):
        for level in ("tich_cuc", "hoat_dong", "khong_hoat_dong", "chua_xep_loai", "tam_nghi"):
            by_sql = {user.id for user in User.query.filter(level_filter(level))}
            by_python = {user.id for user in User.query if level_of(user) == level}
            self.assertEqual(by_sql, by_python, level)

    def test_compute_does_not_write(self):
        before = MemberActivity.query.count()
        self.assertEqual(compute(self.user(2), self.period)["score"], 50.0)
        self.assertEqual(MemberActivity.query.count(), before)

    # ------------------------------------------------------------ cập nhật
    def test_editing_attendance_of_an_ended_event_recomputes(self):
        self.login(1)
        registration = EventRegistration.query.filter_by(event_id=1, user_id=2).one()
        response = self.client.post(f"/event/1/attendance/{registration.id}",
                                    data={"csrf_token": csrf_for(self.client), "status": "on_time"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(summary(self.user(2))["score"], 100.0)
        self.assertEqual(level_of(self.user(2)), "tich_cuc")

    def test_ending_an_event_counts_it(self):
        self.login(1)
        response = self.client.post("/event/3/ket-thuc", data={"csrf_token": csrf_for(self.client)})
        self.assertEqual(response.status_code, 302)
        stats = summary(self.user(4))
        # Task của sự kiện 3 chưa xong (0/1); sự kiện 3 mở cho mọi người, 4 không đăng ký → vắng.
        self.assertEqual((stats["task_done"], stats["task_total"], stats["event_present"],
                          stats["event_total"], stats["level"]), (0, 1, 0, 1, "khong_hoat_dong"))
        # Người 2 có mặt ở sự kiện 3 → 2 sự kiện phải tham gia, có mặt 1.
        self.assertEqual(summary(self.user(2))["event_total"], 2)

    # ------------------------------------------------------------ hiển thị
    def test_member_page_counts_filter_and_export_use_the_same_source(self):
        self.login(1)
        page = self.client.get("/member").get_data(as_text=True)
        self.assertIn("Tạm nghỉ", page)
        self.assertIn("Chưa xếp loại", page)
        filtered = self.client.get("/member?status=hoat_dong").get_data(as_text=True)
        self.assertIn("SE180002", filtered)
        self.assertIn("SE180003", filtered)
        self.assertNotIn("SE180004", filtered)
        workbook = load_workbook(io.BytesIO(self.client.get("/member/export").data))
        rows = {row[1]: row[-1] for row in workbook.active.iter_rows(min_row=2, values_only=True)}
        self.assertEqual(rows["SE180001"], "Tích cực")
        self.assertEqual(rows["SE180006"], "Tạm nghỉ")
        self.assertEqual(self.client.get("/member?status=khong_ton_tai").status_code, 200)

    def test_profile_and_api_show_the_same_numbers(self):
        self.login(2)
        page = self.client.get("/profile").get_data(as_text=True)
        self.assertIn("Hoạt động · 50 điểm", page)
        self.assertIn("2 / 2", page)
        data = self.client.get("/api/v1/members/2/activity").json
        self.assertEqual((data["label"], data["score"], data["task_rate"], data["attendance_rate"]),
                         ("Hoạt động", 50.0, 100.0, 0.0))
        self.assertEqual(self.client.get("/api/v1/members/3/activity").status_code, 403)

    def test_assistant_reads_the_stored_level(self):
        own = answer_by_rules(self.user(2), "tôi có tích cực không")
        self.assertIn("Hoạt động (50 điểm)", own["answer"])
        self.assertIn("Task hoàn thành 2/2", own["answer"])
        count = answer_by_rules(self.user(1), "có bao nhiêu thành viên không hoạt động")
        self.assertIn("Số thành viên (mức Không hoạt động): 0.", count["answer"])
        unsupported = answer_by_rules(self.user(1), "thành viên không hoạt động 30 ngày")
        self.assertEqual(unsupported["verification"], "unsupported")

    def test_removed_point_and_setting_endpoints_are_gone(self):
        self.login(1)
        token = csrf_for(self.client)
        self.assertEqual(self.client.post("/api/v1/members/2/points", json={"points": 5, "reason": "x"},
                                          headers={"X-CSRF-Token": token}).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/settings/activity").status_code, 404)


if __name__ == "__main__":
    unittest.main()
