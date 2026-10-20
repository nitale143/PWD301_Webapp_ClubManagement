"""Durable email delivery behavior and board-only retry controls."""

import tempfile
import unittest
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from tests.support import csrf_for
from app import create_app, db
from app.mail import send_email
from app.mail_models import EmailDelivery
from app.models import EmailProposal, FundPeriod, Notification, User
from app.scheduler import check_period_reminder
from app.services.mail_delivery import (
    DeliveryRetryError,
    _session as delivery_session,
    mark_stale_deliveries_unknown,
    purge_expired_delivery_bodies,
    retry_email_delivery,
)
from app.timeutil import utcnow


class MailDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        database = Path(self.directory.name) / "mail-test.db"
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database}",
            "SECRET_KEY": "mail-delivery-test-key",
            "MAIL_ENABLED": True,
            "MAIL_SERVER": "smtp.example.test",
            "MAIL_PORT": 587,
            "MAIL_USE_TLS": True,
            "MAIL_USE_SSL": False,
            "MAIL_DEFAULT_SENDER": "club@example.test",
        })
        self.client = self.app.test_client()
        self.context = self.app.app_context()
        self.context.push()
        self.board = User(
            id=1, mssv="BOARD1", ho_ten="Board Member", ngay_sinh=date(2000, 1, 1),
            sdt="0123456789", email="board@example.test", status="approved", chuc_vu="CN",
        )
        self.member = User(
            id=2, mssv="MEMBER1", ho_ten="Club Member", ngay_sinh=date(2000, 1, 1),
            sdt="0123456789", email="member@example.test", status="approved", chuc_vu="THANH_VIEN",
        )
        self.board.set_password("test-password")
        self.member.set_password("test-password")
        db.session.add_all([self.board, self.member])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.context.pop()
        self.directory.cleanup()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True

    def post_with_csrf(self, path, data=None):
        payload = dict(data or {})
        payload["csrf_token"] = csrf_for(self.client)
        return self.client.post(path, data=payload, follow_redirects=True)

    def test_success_failure_skipped_and_development_saved_are_tracked(self):
        with patch("app.mail._send_delivery_message", return_value=("sent", None, "accepted")):
            self.assertTrue(send_email("member@example.test", "Hello", "Body"))
        successful = EmailDelivery.query.one()
        self.assertEqual(successful.status, "sent")
        self.assertEqual(successful.attempt_count, 1)
        self.assertTrue(successful.message_id.startswith("<"))

        with patch("app.mail._send_delivery_message", return_value=("failed", "SMTPException", "connect")):
            self.assertFalse(send_email("member@example.test", "Another", "Body"))
        failed = EmailDelivery.query.filter_by(subject="Another").one()
        self.assertEqual(failed.status, "failed")
        self.assertTrue(failed.retry_allowed)

        self.app.config.update(MAIL_ENABLED=False, MAIL_OUTBOX=False)
        self.assertFalse(send_email("member@example.test", "Disabled", "Body"))
        skipped = EmailDelivery.query.filter_by(subject="Disabled").one()
        self.assertEqual(skipped.status, "skipped")

        outbox = Path(self.directory.name) / "outbox"
        outbox.mkdir()
        self.app.instance_path = str(outbox.parent)
        self.app.config.update(MAIL_ENABLED=False, MAIL_OUTBOX=True, APP_ENV="development")
        self.assertTrue(send_email("member@example.test", "Local", "Body"))
        saved = EmailDelivery.query.filter_by(subject="Local").one()
        self.assertEqual(saved.status, "dev-saved")
        self.assertTrue(list((outbox.parent / "mail_outbox").glob("*.eml")))

    def test_same_idempotency_key_and_concurrent_claim_cannot_send_twice(self):
        calls = []

        def smtp_attempt(*args):
            calls.append(args)
            # Simulate a second worker reaching the same durable claim mid-send.
            self.assertFalse(send_email(
                "member@example.test", "Once", "Body", idempotency_key="same-logical-send"
            ))
            return "sent", None, "accepted"

        with patch("app.mail._send_delivery_message", side_effect=smtp_attempt):
            self.assertTrue(send_email(
                "member@example.test", "Once", "Body", idempotency_key="same-logical-send"
            ))
            self.assertTrue(send_email(
                "member@example.test", "Once", "Body", idempotency_key="same-logical-send"
            ))

        row = EmailDelivery.query.filter_by(idempotency_key="same-logical-send").one()
        self.assertEqual(len(calls), 1)
        self.assertEqual(row.status, "sent")
        self.assertEqual(row.attempt_count, 1)

    def test_unknown_delivery_needs_explicit_confirmation_and_reuses_message_id(self):
        with patch("app.mail._send_delivery_message", side_effect=[
            ("unknown", "TimeoutError", "send"), ("sent", None, "accepted")
        ]) as attempt:
            self.assertFalse(send_email(
                "member@example.test", "Maybe delivered", "Body", idempotency_key="uncertain-send"
            ))
            row = EmailDelivery.query.filter_by(idempotency_key="uncertain-send").one()
            self.assertEqual(row.status, "unknown")
            self.assertFalse(send_email(
                "member@example.test", "Maybe delivered", "Body", idempotency_key="uncertain-send"
            ))
            self.assertEqual(attempt.call_count, 1)

            self.login(self.board.id)
            denied = self.post_with_csrf(f"/mail-deliveries/{row.id}/retry")
            self.assertEqual(row.status, "unknown")
            self.assertEqual(attempt.call_count, 1)
            self.assertIn("xác nhận trước khi thử lại".encode(), denied.data)

            retried = self.post_with_csrf(
                f"/mail-deliveries/{row.id}/retry", {"confirm_unknown": "yes"}
            )
            db.session.refresh(row)
            self.assertEqual(row.status, "sent")
            self.assertEqual(row.attempt_count, 2)
            self.assertEqual(row.unknown_confirmed_by_id, self.board.id)
            self.assertEqual(attempt.call_args_list[0].args[3], attempt.call_args_list[1].args[3])
            self.assertIn("Email đã gửi thành công".encode(), retried.data)

    def test_stale_unknown_confirmation_cannot_claim_after_an_aba_transition(self):
        # WAL permits the nested retry to commit while the outer claim has read
        # its stale snapshot but has not yet issued its compare-and-swap UPDATE.
        with db.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            connection.commit()

        with patch("app.mail._send_delivery_message", return_value=("unknown", "TimeoutError", "send")) as attempt:
            self.assertFalse(send_email(
                "member@example.test", "ABA claim", "Body", idempotency_key="aba-claim"
            ))
            delivery = EmailDelivery.query.filter_by(idempotency_key="aba-claim").one()
            did_interleave = False
            original_execute = Session.execute

            def retry_before_stale_claim(session, statement, *args, **kwargs):
                nonlocal did_interleave
                assignment_keys = {
                    getattr(key, "key", key) for key in getattr(statement, "_values", {})
                }
                if (not did_interleave
                        and getattr(getattr(statement, "table", None), "name", None) == "email_delivery"
                        and "attempt_count" in assignment_keys):
                    did_interleave = True
                    self.assertFalse(retry_email_delivery(
                        delivery.id, self.board.id, confirm_unknown=True
                    ))
                return original_execute(session, statement, *args, **kwargs)

            with patch.object(Session, "execute", new=retry_before_stale_claim):
                with self.assertRaises(DeliveryRetryError):
                    retry_email_delivery(delivery.id, self.board.id, confirm_unknown=True)

            db.session.refresh(delivery)
            self.assertTrue(did_interleave)
            self.assertEqual(attempt.call_count, 2)  # initial send + nested confirmed retry
            self.assertEqual(delivery.status, "unknown")
            self.assertEqual(delivery.attempt_count, 2)

    def test_old_in_flight_outcome_cannot_overwrite_a_newer_attempt(self):
        with db.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            connection.commit()

        calls = 0

        def stale_then_retry(*_args):
            nonlocal calls
            calls += 1
            if calls == 1:
                with delivery_session() as session:
                    delivery = session.query(EmailDelivery).filter_by(
                        idempotency_key="stale-outcome"
                    ).one()
                    delivery.last_attempt_at = utcnow() - timedelta(minutes=4)
                    session.commit()
                self.assertEqual(mark_stale_deliveries_unknown(), 1)
                self.assertFalse(retry_email_delivery(
                    delivery.id, self.board.id, confirm_unknown=True
                ))
                # The old in-flight SMTP call reports success after the newer
                # retry has already recorded its own failed outcome.
                return "sent", None, "accepted"
            return "failed", "SMTPException", "connect"

        with patch("app.mail._send_delivery_message", side_effect=stale_then_retry):
            self.assertFalse(send_email(
                "member@example.test", "Stale result", "Body", idempotency_key="stale-outcome"
            ))

        delivery = EmailDelivery.query.filter_by(idempotency_key="stale-outcome").one()
        self.assertEqual(calls, 2)
        self.assertEqual(delivery.status, "failed")
        self.assertEqual(delivery.attempt_count, 2)

    def test_sensitive_recovery_content_is_neither_stored_nor_rendered_for_retry(self):
        token = "reset-token-should-never-appear"
        with patch("app.mail._send_delivery_message", return_value=("failed", "SMTPException", "connect")):
            self.assertFalse(send_email(
                "member@example.test", "Đặt lại mật khẩu",
                f"Mở https://example.test/password/reset/{token} để đổi mật khẩu."
            ))
            self.assertFalse(send_email(
                "member@example.test", "Mật khẩu tạm",
                "Mật khẩu tạm dùng một lần là: TempSecret456"
            ))
        row = EmailDelivery.query.filter_by(subject="[Nội dung nhạy cảm đã ẩn]").first()
        self.assertTrue(row.sensitive_content)
        self.assertFalse(row.retry_allowed)
        self.assertIsNone(row.body)
        credential = EmailDelivery.query.filter_by(recipient_email="member@example.test").order_by(
            EmailDelivery.id.desc()
        ).first()
        self.assertTrue(credential.sensitive_content)
        self.assertIsNone(credential.body)

        self.login(self.board.id)
        response = self.client.get("/mail-deliveries/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(token.encode(), response.data)
        self.assertNotIn(b"TempSecret456", response.data)
        self.assertIn("Nội dung nhạy cảm đã được ẩn".encode(), response.data)

    def test_sensitive_initial_send_uses_original_content_while_database_stays_redacted(self):
        subject = "Đặt lại mật khẩu"
        token = "private-reset-token-value"
        body = f"Mở https://example.test/password/reset/{token} để tiếp tục."
        smtp = MagicMock()
        smtp.send_message.return_value = {}

        with patch("app.mail.smtplib.SMTP", return_value=smtp):
            self.assertTrue(send_email("member@example.test", subject, body))

        message = smtp.send_message.call_args.args[0]
        self.assertIsInstance(message, EmailMessage)
        self.assertEqual(message["Subject"], subject)
        self.assertIn(token, message.get_content())
        row = EmailDelivery.query.one()
        self.assertEqual(message["Message-ID"], row.message_id)
        self.assertEqual(row.status, "sent")
        self.assertIsNone(row.body)
        self.assertNotEqual(row.subject, subject)

    def test_retry_rechecks_recipient_address_and_active_status(self):
        second = User(
            id=3, mssv="MEMBER2", ho_ten="Second Member", ngay_sinh=date(2000, 1, 1),
            sdt="0123456789", email="second@example.test", status="approved", chuc_vu="THANH_VIEN",
        )
        second.set_password("test-password")
        db.session.add(second)
        db.session.commit()
        with patch("app.mail._send_delivery_message", return_value=("failed", "SMTPException", "connect")) as attempt:
            self.assertFalse(send_email("member@example.test", "Address check", "Body"))
            self.assertFalse(send_email("second@example.test", "Activity check", "Body"))
            address_row = EmailDelivery.query.filter_by(subject="Address check").one()
            inactive_row = EmailDelivery.query.filter_by(subject="Activity check").one()

            self.member.email = "changed@example.test"
            second.status = "rejected"
            db.session.commit()
            self.login(self.board.id)

            self.post_with_csrf(f"/mail-deliveries/{address_row.id}/retry")
            self.post_with_csrf(f"/mail-deliveries/{inactive_row.id}/retry")
            self.assertEqual(attempt.call_count, 2)
            self.assertEqual(address_row.status, "failed")
            self.assertEqual(inactive_row.status, "failed")

    def test_failed_retry_body_expires_after_bounded_retention(self):
        with patch("app.mail._send_delivery_message", return_value=("failed", "SMTPException", "connect")):
            self.assertFalse(send_email("member@example.test", "Private draft", "Body"))
        row = EmailDelivery.query.one()
        self.assertEqual(row.body, "Body")
        self.assertGreater(row.body_expires_at, row.created_at)

        row.body_expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        self.assertEqual(purge_expired_delivery_bodies(), 1)
        db.session.refresh(row)
        self.assertIsNone(row.body)
        self.assertFalse(row.retry_allowed)

    def test_period_reminder_commits_business_notifications_before_delivery_tracking(self):
        today = date.today()
        period = FundPeriod(
            ten_ky="Current", ngay_bat_dau=today, ngay_ket_thuc=today + timedelta(days=1),
            is_current=True, da_nhac_ky_moi=False,
        )
        db.session.add(period)
        db.session.commit()

        with patch("app.mail._send_delivery_message", return_value=("sent", None, "accepted")) as attempt:
            self.assertEqual(check_period_reminder(self.app), 1)

        self.assertEqual(Notification.query.count(), 1)
        self.assertTrue(db.session.get(FundPeriod, period.id).da_nhac_ky_moi)
        delivery = EmailDelivery.query.one()
        self.assertEqual(delivery.status, "sent")
        attempt.assert_called_once()

    def test_board_permission_is_required_for_delivery_history_and_retry(self):
        with patch("app.mail._send_delivery_message", return_value=("failed", "SMTPException", "connect")):
            self.assertFalse(send_email("member@example.test", "Review", "Body"))
        row = EmailDelivery.query.one()

        self.login(self.member.id)
        self.assertEqual(self.client.get("/mail-deliveries/").status_code, 403)
        denied = self.post_with_csrf(f"/mail-deliveries/{row.id}/retry")
        self.assertEqual(denied.status_code, 403)

    def test_email_proposal_retry_remains_under_its_existing_review_gate(self):
        draft = EmailProposal(
            recipient_email="member@example.test", request_text="Reminder",
            subject="Proposal message", body="Reviewed body", status="sending",
            created_by_id=self.board.id,
        )
        db.session.add(draft)
        db.session.commit()

        with patch("app.mail._send_delivery_message", return_value=("failed", "SMTPException", "connect")) as attempt:
            self.assertFalse(send_email(draft.recipient_email, draft.subject, draft.body))
            delivery = EmailDelivery.query.one()
            self.assertFalse(delivery.retry_allowed)

            self.login(self.board.id)
            response = self.post_with_csrf(f"/mail-deliveries/{delivery.id}/retry")
            self.assertEqual(attempt.call_count, 1)
            self.assertEqual(delivery.status, "failed")
            self.assertIn("không có nội dung an toàn".encode(), response.data)


if __name__ == "__main__":
    unittest.main()
