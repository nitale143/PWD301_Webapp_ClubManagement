import tempfile
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from app import create_app, db
from flask import g
from app.models import (
    AIProposal, EmailProposal, Event, FundCollection, FundPayment, Notification,
    Task, User,
)
from app.scheduler import check_deadlines
from app.services.common import audit
from tests.support import csrf_for


class OperationsNotificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        database_path = Path(self.directory.name) / "operations-notifications.db"
        self.app = create_app({
            "TESTING": True,
            "AUTO_CREATE_TABLES": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path}",
            "SECRET_KEY": "operations-notifications-test-key",
            "SCHEDULER_ENABLED": False,
            "MAIL_ENABLED": False,
            "AI_PROVIDER": "rules",
            "DEADLINE_WARNING_DAYS": 7,
        })
        self.client = self.app.test_client()
        self.context = self.app.app_context()
        self.context.push()
        self.add_user(1, "CN", "Chủ nhiệm")
        self.add_user(2, "TB", "Trưởng ban")
        self.add_user(3, "THUKY_THUQUY", "Thủ quỹ")
        self.add_user(4, "THANH_VIEN", "Thành viên")
        self.add_user(5, "THANH_VIEN", "Hồ sơ đang chờ", status="pending")
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        self.directory.cleanup()

    def add_user(self, user_id, role, name, status="approved"):
        user = User(
            id=user_id,
            mssv=f"SV{user_id:03d}",
            ho_ten=name,
            ngay_sinh=date(2003, 1, 1),
            sdt=f"09000000{user_id:02d}",
            email=f"member{user_id}@example.test",
            password_hash="test-hash",
            status=status,
            chuc_vu=role,
        )
        db.session.add(user)
        return user

    def login(self, user_id):
        user = db.session.get(User, user_id)
        with self.client.session_transaction() as session:
            session["_user_id"] = user.get_id()
            session["_fresh"] = True
        g.pop("_login_user", None)

    def csrf_headers(self):
        return {"X-CSRF-Token": csrf_for(self.client)}

    def add_event(self, event_id, status="sap_dien_ra", owner_id=1):
        event = Event(
            id=event_id,
            ten_su_kien=f"Sự kiện {event_id}",
            ma_su_kien=f"EV-{event_id}",
            thoi_gian_bat_dau=datetime(2026, 11, 1, 9),
            thoi_gian_ket_thuc=datetime(2026, 11, 1, 12),
            trang_thai=status,
            tao_boi_id=owner_id,
        )
        db.session.add(event)
        return event

    def test_operations_inbox_is_board_only_and_scopes_each_section(self):
        event = self.add_event(1)
        cancelled_event = self.add_event(2, status="cancelled")
        db.session.add_all([
            Task(id=1, event_id=event.id, ten_task="Task cần duyệt", deadline=datetime(2026, 11, 2),
                 assignee_id=4, trang_thai="cho_duyet"),
            Task(id=2, event_id=cancelled_event.id, ten_task="Task đã hủy sự kiện",
                 deadline=datetime(2026, 11, 2), assignee_id=4, trang_thai="cho_duyet"),
            FundCollection(
                id=1, ten_khoan_thu="Quỹ cần xác nhận", loai="other", so_tien=Decimal("100"),
                ngay_bat_dau=date(2026, 10, 1), han_dong=date(2026, 11, 1),
                tao_boi_id=1, trang_thai="open",
            ),
            FundPayment(
                id=1, fund_id=1, user_id=4, so_tien=Decimal("100"),
                ngay_dong=datetime(2026, 10, 20), phuong_thuc="bank",
                trang_thai="pending",
            ),
            AIProposal(
                id=1, kind="task", request_text="Chia việc",
                payload={"tasks": []}, provider="rules", status="pending", created_by_id=4,
            ),
            EmailProposal(
                id=1, recipient_email="recipient@example.test", request_text="Gửi email",
                subject="Email đang chờ", body="Nội dung", status="pending", created_by_id=1,
            ),
        ])
        db.session.commit()

        self.login(4)
        self.assertEqual(self.client.get("/operations").status_code, 403)

        self.login(2)
        response = self.client.get("/operations")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Task cần duyệt", body)
        self.assertNotIn("Task đã hủy sự kiện", body)
        self.assertIn("Đề xuất AI chờ duyệt", body)
        self.assertNotIn("Hồ sơ đang chờ", body)
        self.assertNotIn("Quỹ cần xác nhận", body)
        self.assertIn("Email đang chờ", body)

        self.login(1)
        response = self.client.get("/operations")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Hồ sơ đang chờ", body)
        self.assertIn("Quỹ cần xác nhận", body)
        self.assertIn("Email đang chờ", body)

    def test_notification_page_is_private_and_read_actions_require_post_and_csrf(self):
        db.session.add_all([
            Notification(id=1, user_id=1, noi_dung="Thông báo của tôi", da_doc=False),
            Notification(id=2, user_id=2, noi_dung="Thông báo riêng của người khác", da_doc=False),
        ])
        db.session.commit()
        self.login(1)

        response = self.client.get("/notifications")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Thông báo của tôi", body)
        self.assertNotIn("Thông báo riêng của người khác", body)
        self.assertFalse(db.session.get(Notification, 1).da_doc)
        self.assertEqual(self.client.get("/notifications/1/read").status_code, 405)
        self.assertEqual(self.client.post("/notifications/1/read").status_code, 400)

        foreign = self.client.post("/notifications/2/read", headers=self.csrf_headers())
        self.assertEqual(foreign.status_code, 404)
        self.assertFalse(db.session.get(Notification, 2).da_doc)

        marked = self.client.post("/notifications/1/read", headers=self.csrf_headers())
        self.assertEqual(marked.status_code, 302)
        self.assertTrue(db.session.get(Notification, 1).da_doc)
        self.client.post("/notifications/read-all", headers=self.csrf_headers())
        self.assertEqual(Notification.query.filter_by(user_id=1, da_doc=False).count(), 0)

    def test_topbar_counts_unread_notifications_beyond_recent_preview(self):
        now = datetime(2026, 10, 20, 10)
        db.session.add_all([
            Notification(
                user_id=1, noi_dung=f"Thông báo {index}", da_doc=index != 9,
                thoi_gian=now - timedelta(minutes=index),
            )
            for index in range(10)
        ])
        db.session.commit()
        self.login(1)

        response = self.client.get("/notifications")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('aria-label="Xem thông báo, 1 chưa đọc"', body)
        self.assertIn("Thông báo 9", body)

    def test_notifications_pagination_labels_follow_newest_first_order(self):
        now = datetime(2026, 10, 20, 10)
        db.session.add_all([
            Notification(
                user_id=1, noi_dung=f"Thông báo trang {index}", da_doc=True,
                thoi_gian=now - timedelta(minutes=index),
            )
            for index in range(30)
        ])
        db.session.commit()
        self.login(1)

        first = self.client.get("/notifications")
        first_body = first.get_data(as_text=True)
        self.assertIn('href="/notifications?page=2">Thông báo cũ hơn</a>', first_body)
        self.assertIn("Thông báo trang 0", first_body)
        self.assertNotIn("Thông báo trang 29", first_body)

        second = self.client.get("/notifications?page=2")
        second_body = second.get_data(as_text=True)
        self.assertIn('href="/notifications?page=1">Thông báo mới hơn</a>', second_body)
        self.assertIn("Thông báo trang 29", second_body)
        self.assertNotIn("Thông báo trang 0", second_body)

    def test_operations_task_section_is_bounded_and_paginated(self):
        event = self.add_event(1)
        db.session.add_all([
            Task(
                id=task_id,
                event_id=event.id,
                ten_task=f"Task {task_id}",
                deadline=datetime(2026, 11, 2),
                assignee_id=4,
                trang_thai="cho_duyet",
            )
            for task_id in range(1, 12)
        ])
        db.session.commit()
        self.login(2)

        first = self.client.get("/operations")
        first_body = first.get_data(as_text=True)
        self.assertEqual(first.status_code, 200)
        self.assertIn('href="/operations?tasks_page=2">Mục tiếp</a>', first_body)
        self.assertIn("Task 1</strong>", first_body)
        self.assertNotIn("Task 11</strong>", first_body)

        second = self.client.get("/operations?tasks_page=2")
        second_body = second.get_data(as_text=True)
        self.assertIn("Task 11</strong>", second_body)
        self.assertNotIn("Task 1</strong>", second_body)

    def test_task_audit_notification_shares_transaction_and_deadline_job_deduplicates(self):
        event = self.add_event(1)
        task = Task(
            id=1, event_id=event.id, ten_task="Thiết kế poster",
            deadline=datetime(2026, 10, 27, 9), assignee_id=4,
            trang_thai="dang_lam",
        )
        cancelled_event = self.add_event(2, status="cancelled")
        cancelled_task = Task(
            id=2, event_id=cancelled_event.id, ten_task="Task đã hủy",
            deadline=datetime(2026, 10, 27, 9), assignee_id=3,
            trang_thai="dang_lam",
        )
        db.session.add_all([task, cancelled_task])
        db.session.flush()
        db.session.commit()

        audit(db.session.get(User, 1), "task_assigned", task)
        db.session.flush()
        self.assertEqual(Notification.query.filter_by(user_id=4).count(), 1)
        db.session.rollback()
        self.assertEqual(Notification.query.filter_by(user_id=4).count(), 0)

        with patch("app.scheduler.club_now", return_value=datetime(2026, 10, 20, 7)), \
             patch("app.scheduler.send_email", return_value=True) as send_email:
            self.assertEqual(check_deadlines(self.app), 1)
            self.assertEqual(check_deadlines(self.app), 0)
        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(Notification.query.filter_by(user_id=4).count(), 1)
        self.assertEqual(Notification.query.filter_by(user_id=3).count(), 0)


if __name__ == "__main__":
    unittest.main()
