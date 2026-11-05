"""Trạng thái sự kiện: BĐH chuyển tay + hệ thống tự rà soát theo giờ, đồng bộ mọi nơi."""

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from flask import g

from tests.support import csrf_for
from app import create_app, db
from app.models import (
    Attendance, AuditLog, Ban, Event, EventRegistration, FundPeriod, MemberActivity, MemberRecord,
    User, UserBan,
)
from app.services.assistant import RulesProvider
from app.services.club_data_tools import answer_by_rules
from app.services.common import DomainError, club_now
from app.services.event_status import (
    CANCELLED, ENDED, ONGOING, UPCOMING, auto_close_at, change_status, display, due_status,
    initial_status, sync_statuses,
)


class EventStatusTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "event-status-secret-key-0123456789abcdef",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(self.directory.name) / 'status.db'}",
            "AI_PROVIDER": "rules", "SCHEDULER_ENABLED": False, "EVENT_AUTO_CLOSE_HOURS": 24})
        self.client = self.app.test_client()
        self.context = self.app.test_request_context()
        self.context.push()
        self.now = club_now()
        db.session.add_all([
            Ban(id=1, ten_ban="Kỹ thuật"),
            FundPeriod(id=1, ten_ky="Fall 2026", ngay_bat_dau=(self.now - timedelta(days=60)).date(),
                       ngay_ket_thuc=(self.now + timedelta(days=60)).date(), is_current=True),
        ])
        for user_id, role in ((1, "CN"), (2, "THANH_VIEN")):
            user = User(id=user_id, mssv=f"SE18000{user_id}", ho_ten=f"Member {user_id}",
                        ngay_sinh=date(2004, 1, 1), sdt=f"+8490000000{user_id}",
                        email=f"m{user_id}@example.com", status="approved", chuc_vu=role)
            user.set_password("test-password")
            db.session.add(user)
            db.session.add(MemberRecord(user_id=user_id, ngay_gia_nhap=date(2025, 1, 1)))
            db.session.add(UserBan(user_id=user_id, ban_id=1))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.directory.cleanup()

    def event(self, start_hours, length_hours=2, status=None, code=None):
        start = self.now + timedelta(hours=start_hours)
        event = Event(ten_su_kien=code or "Workshop", ma_su_kien=code or f"E{Event.query.count() + 1}",
                      thoi_gian_bat_dau=start, thoi_gian_ket_thuc=start + timedelta(hours=length_hours),
                      trang_thai=status or initial_status(start, now=self.now), tao_boi_id=1)
        db.session.add(event)
        db.session.commit()
        return event

    def user(self, user_id):
        return db.session.get(User, user_id)

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
        g.pop("_login_user", None)

    def post_status(self, event, target):
        return self.client.post(f"/event/{event.id}/trang-thai",
                                data={"csrf_token": csrf_for(self.client), "trang_thai": target})

    # ------------------------------------------------------------ quy tắc theo giờ
    def test_time_rules(self):
        self.assertEqual(initial_status(self.now + timedelta(hours=1), now=self.now), UPCOMING)
        self.assertEqual(initial_status(self.now - timedelta(hours=1), now=self.now), ONGOING)
        upcoming = self.event(1)
        self.assertIsNone(due_status(upcoming, self.now))
        self.assertEqual(due_status(upcoming, self.now + timedelta(hours=1)), ONGOING)
        # Quá giờ kết thúc nhưng chưa hết 24 giờ chờ: vẫn đang diễn ra, hiển thị "Chờ kết thúc".
        later = self.now + timedelta(hours=10)
        upcoming.trang_thai = ONGOING
        self.assertIsNone(due_status(upcoming, later))
        self.assertEqual(display(upcoming, later)["label"], "Chờ kết thúc")
        self.assertEqual(auto_close_at(upcoming), upcoming.thoi_gian_ket_thuc + timedelta(hours=24))
        self.assertEqual(due_status(upcoming, upcoming.thoi_gian_ket_thuc + timedelta(hours=24)), ENDED)
        # Sự kiện bị bỏ quên nhiều ngày: nhảy thẳng tới đã kết thúc.
        old = self.event(-100, status=UPCOMING)
        self.assertEqual(due_status(old, self.now), ENDED)

    def test_sync_moves_forward_only_and_records_audit(self):
        starts = self.event(-1, status=UPCOMING, code="START")
        closes = self.event(-30, status=ONGOING, code="CLOSE")
        stays = self.event(5, code="LATER")
        cancelled = self.event(-50, status=CANCELLED, code="CXL")
        self.assertEqual(sync_statuses(), 2)
        self.assertEqual([db.session.get(Event, e.id).trang_thai for e in (starts, closes, stays, cancelled)],
                         [ONGOING, ENDED, UPCOMING, CANCELLED])
        sources = {row.details["source"] for row in AuditLog.query.filter_by(action="event_status")}
        self.assertEqual(sources, {"auto"})
        self.assertEqual(sync_statuses(), 0)

    def test_auto_close_recomputes_activity(self):
        event = self.event(-30, status=ONGOING)
        registration = EventRegistration(event_id=event.id, user_id=2, trang_thai="registered")
        db.session.add(registration)
        db.session.flush()
        db.session.add(Attendance(registration_id=registration.id, trang_thai="on_time", checkin_boi_id=1))
        db.session.commit()
        sync_statuses()
        row = MemberActivity.query.filter_by(user_id=2, period_id=1).one()
        self.assertEqual((row.su_kien_co_mat, row.tong_su_kien, row.trang_thai), (1, 1, "tich_cuc"))

    def test_page_visit_triggers_throttled_sync(self):
        event = self.event(-1, status=UPCOMING)
        self.login(2)
        self.client.get("/home")
        self.assertEqual(db.session.get(Event, event.id).trang_thai, ONGOING)
        other = self.event(-1, status=UPCOMING, code="SECOND")
        self.client.get("/home")      # trong 60 giây: không rà soát toàn bộ lại
        self.assertEqual(db.session.get(Event, other.id).trang_thai, UPCOMING)
        self.client.get(f"/event/{other.id}")   # mở trang chi tiết: áp ngay cho sự kiện đó
        self.assertEqual(db.session.get(Event, other.id).trang_thai, ONGOING)

    # ------------------------------------------------------------ chuyển tay
    def test_board_starts_early_ends_reopens_and_auto_close_restarts_from_reopen(self):
        event = self.event(3)
        self.login(1)
        self.assertEqual(self.post_status(event, ONGOING).status_code, 302)
        self.assertEqual(db.session.get(Event, event.id).trang_thai, ONGOING)
        sync_statuses()                      # bắt đầu sớm không bị hệ thống kéo về "sắp diễn ra"
        self.assertEqual(db.session.get(Event, event.id).trang_thai, ONGOING)
        self.post_status(event, ENDED)
        self.assertEqual(db.session.get(Event, event.id).trang_thai, ENDED)
        self.post_status(event, ONGOING)     # mở lại
        reopened = db.session.get(Event, event.id)
        self.assertEqual(reopened.trang_thai, ONGOING)
        self.assertIsNotNone(reopened.trang_thai_doi_tay_luc)
        # Mở lại sau giờ kết thúc → tự kết thúc lại 24 giờ sau lúc mở.
        reopened.thoi_gian_bat_dau = self.now - timedelta(hours=40)
        reopened.thoi_gian_ket_thuc = self.now - timedelta(hours=30)
        db.session.commit()
        self.assertIsNone(due_status(reopened, self.now + timedelta(hours=1)))
        self.assertEqual(due_status(reopened, reopened.trang_thai_doi_tay_luc + timedelta(hours=24)), ENDED)

    def test_invalid_transitions_and_permissions(self):
        event = self.event(3)
        self.login(2)
        self.assertEqual(self.post_status(event, ENDED).status_code, 403)
        self.login(1)
        response = self.post_status(event, "sap_dien_ra")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(db.session.get(Event, event.id).trang_thai, UPCOMING)
        change_status(self.user(1), event, CANCELLED)
        with self.assertRaises(DomainError):
            change_status(self.user(1), event, ONGOING)       # đã hủy thì không mở lại

    def test_cannot_cancel_after_attendance(self):
        event = self.event(-1, status=ONGOING)
        registration = EventRegistration(event_id=event.id, user_id=2, trang_thai="registered")
        db.session.add(registration)
        db.session.flush()
        db.session.add(Attendance(registration_id=registration.id, trang_thai="late", checkin_boi_id=1))
        db.session.commit()
        with self.assertRaises(DomainError):
            change_status(self.user(1), event, CANCELLED)

    # ------------------------------------------------------------ đồng bộ hiển thị
    def test_pages_api_and_assistant_show_the_same_status(self):
        early = self.event(5, code="EARLY")
        cancelled = self.event(10, code="CXL")
        waiting = self.event(-5, length_hours=2, status=ONGOING, code="WAIT")
        change_status(self.user(1), early, ONGOING)
        change_status(self.user(1), cancelled, CANCELLED)
        self.login(1)
        page = self.client.get("/event").get_data(as_text=True)
        self.assertIn("Đã hủy", page)
        self.assertIn("Chờ kết thúc", page)
        detail = self.client.get(f"/event/{waiting.id}").get_data(as_text=True)
        self.assertIn("Kết thúc sự kiện", detail)
        self.assertIn("nếu chưa ai kết thúc", detail)
        data = self.client.get(f"/api/v1/events/{early.id}").json
        self.assertEqual((data["status"], data["status_label"]), (ONGOING, "Đang diễn ra"))
        ongoing = answer_by_rules(self.user(1), "Sự kiện nào đang diễn ra?")["answer"]
        self.assertIn("EARLY", ongoing)          # bắt đầu sớm → đang diễn ra dù chưa tới giờ
        self.assertNotIn("WAIT", ongoing)        # đã qua giờ kết thúc → thuộc "đã diễn ra"
        past = answer_by_rules(self.user(1), "Các sự kiện đã diễn ra gần đây")["answer"]
        self.assertIn("WAIT", past)
        upcoming = RulesProvider().answer(self.user(1), "Các sự kiện sắp diễn ra")["answer"]
        self.assertNotIn("EARLY", upcoming)
        self.assertNotIn("CXL", upcoming)

    def test_api_status_endpoint_and_reschedule(self):
        event = self.event(3)
        self.login(1)
        token = csrf_for(self.client)
        started = self.client.post(f"/api/v1/events/{event.id}/status", json={"status": ONGOING},
                                   headers={"X-CSRF-Token": token})
        self.assertEqual(started.json["status"], ONGOING, started.json)
        bad = self.client.post(f"/api/v1/events/{event.id}/status", json={"status": "draft"},
                               headers={"X-CSRF-Token": token})
        self.assertEqual(bad.status_code, 400)
        # Dời giờ sang tuần sau → trạng thái tính lại theo giờ mới.
        start = self.now + timedelta(days=7)
        moved = self.client.patch(f"/api/v1/events/{event.id}", headers={"X-CSRF-Token": token},
                                  json={"start_at": start.isoformat(),
                                        "end_at": (start + timedelta(hours=2)).isoformat()})
        self.assertEqual(moved.json["status"], UPCOMING, moved.json)

    def test_feedback_opens_after_auto_close(self):
        event = self.event(-30, status=ONGOING)
        db.session.add(EventRegistration(event_id=event.id, user_id=2, trang_thai="registered"))
        db.session.commit()
        self.login(2)
        page = self.client.get(f"/event/{event.id}").get_data(as_text=True)
        self.assertIn("Đánh giá sự kiện", page)


if __name__ == "__main__":
    unittest.main()
