"""Deterministic Vietnamese date parsing and task-proposal continuation checks."""

import tempfile
import unittest
from contextlib import ExitStack
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from flask import g

from tests.support import csrf_for
from app import create_app, db
from app.models import (
    AIProposal,
    AssistantPending,
    Ban,
    ChatMessage,
    ConversationPending,
    Event,
    MemberRecord,
    Task,
    User,
    UserBan,
)
from app.services.common import DomainError
from app.services.natural_proposals import _brief, _dates


FIXED_NOW = datetime(2026, 10, 5, 12, 0)


class NaturalDateTimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        database_path = Path(self.directory.name) / "natural-datetime-test.db"
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path}",
            "SECRET_KEY": "test-only-key",
            "AI_PROVIDER": "rules",
            "AI_STRICT_FACTS": True,
            "AI_CHAT_MESSAGES_PER_MINUTE": 100,
            "APP_TIMEZONE": "Asia/Ho_Chi_Minh",
            # Test đóng băng đồng hồ ở tháng 10/2026; rà soát theo giờ thật sẽ kết thúc sự kiện mẫu.
            "EVENT_STATUS_AUTO_SYNC": False,
        })
        self.client = self.app.test_client()
        self.context = self.app.app_context()
        self.context.push()

        db.session.add(Ban(id=1, ten_ban="Truyền thông"))
        self._add_user(1, "SV001", "Lê Chủ tịch", "CN")
        self._add_user(2, "SV002", "Vương Phan", "THANH_VIEN")
        db.session.add(UserBan(user_id=2, ban_id=1))
        db.session.add(Event(
            id=1,
            ten_su_kien="Workshop tuyển thành viên",
            ma_su_kien="EVT26",
            thoi_gian_bat_dau=datetime(2026, 10, 9, 18),
            thoi_gian_ket_thuc=datetime(2026, 10, 12, 23),
            trang_thai="sap_dien_ra",
            tao_boi_id=1,
        ))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.context.pop()
        self.directory.cleanup()

    def _add_user(self, user_id, code, name, role):
        user = User(
            id=user_id,
            mssv=code,
            ho_ten=name,
            ngay_sinh=date(2000, 1, 1),
            sdt=f"09000000{user_id:02d}",
            email=f"member{user_id}@example.test",
            status="approved",
            chuc_vu=role,
        )
        user.set_password("test-password")
        db.session.add(user)
        db.session.add(MemberRecord(user_id=user_id, ngay_gia_nhap=date(2024, 1, 1)))

    def _fixed_clock(self):
        """Freeze parser and proposal validation clocks at the same club-local instant."""
        stack = ExitStack()
        for target in (
            "app.services.natural_proposals.club_now",
            "app.services.natural_datetime.club_now",
            "app.services.proposals.club_now",
        ):
            stack.enter_context(patch(target, return_value=FIXED_NOW, create=True))
        return stack

    def _parse(self, message):
        with self._fixed_clock():
            return _dates(message)

    def _assert_one_date(self, message, expected):
        result = self._parse(message)
        self.assertEqual(len(result), 1, result)
        when, raw_phrase = result[0]
        self.assertEqual(when, expected)
        self.assertIsInstance(raw_phrase, str)
        self.assertTrue(raw_phrase.strip())

    def test_date_and_time_order_and_vietnamese_time_forms(self):
        cases = (
            ("23:00 10/10", datetime(2026, 10, 10, 23, 0)),
            ("10/10 lúc 23:00", datetime(2026, 10, 10, 23, 0)),
            ("10/10 lúc 23h", datetime(2026, 10, 10, 23, 0)),
            ("10/10 lúc 23h30", datetime(2026, 10, 10, 23, 30)),
            ("23h ngày 10/10", datetime(2026, 10, 10, 23, 0)),
            ("11 giờ tối ngày 10/10", datetime(2026, 10, 10, 23, 0)),
            ("tối 23:00 10/10", datetime(2026, 10, 10, 23, 0)),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                self._assert_one_date(message, expected)

    def test_today_and_tomorrow_are_relative_to_fixed_club_date(self):
        self._assert_one_date("hôm nay lúc 23:00", datetime(2026, 10, 5, 23, 0))
        self._assert_one_date("ngày mai 11 giờ tối", datetime(2026, 10, 6, 23, 0))

    def test_full_iso_and_vietnamese_date_formats_remain_supported(self):
        cases = (
            ("2026-10-10 23:00", datetime(2026, 10, 10, 23, 0)),
            ("2026-10-10T23:00", datetime(2026, 10, 10, 23, 0)),
            ("10/10/2026 lúc 23:00", datetime(2026, 10, 10, 23, 0)),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                self._assert_one_date(message, expected)

    def test_event_time_range_uses_one_explicit_date_for_both_times(self):
        result = self._parse("Ngày 10/10/2026 từ 19:00 đến 21:00")
        self.assertEqual(len(result), 2, result)
        self.assertEqual([when for when, _raw in result], [
            datetime(2026, 10, 10, 19, 0),
            datetime(2026, 10, 10, 21, 0),
        ])
        self.assertTrue(all(raw.strip() for _when, raw in result))
        message = "Tạo sự kiện Workshop onboarding ngày 10/10/2026 từ 19:00 đến 21:00"
        raw_phrases = [raw for _when, raw in self._parse(message)]
        self.assertEqual(_brief(message, "event", date_parts=raw_phrases), "Workshop onboarding")

    def test_missing_year_uses_current_club_year_without_rolling_past_dates(self):
        self._assert_one_date("23:00 10/10", datetime(2026, 10, 10, 23, 0))
        # April 10 is already past on the fixed October 5 clock; the parser
        # must still use 2026 instead of silently inventing 2027.
        self._assert_one_date("10/04 lúc 09:00", datetime(2026, 4, 10, 9, 0))

    def test_invalid_calendar_and_clock_values_are_rejected(self):
        for message in (
            "32/10/2026 lúc 23:00",
            "10/13/2026 lúc 23:00",
            "29/02/2026 lúc 12:00",
            "10/10/2026 lúc 24:00",
            "10/10/2026 lúc 23:60",
            "10/10 23h:75",
            "10/10 12:00:99",
        ):
            with self.subTest(message=message):
                with self.assertRaises(DomainError):
                    self._parse(message)

    def test_relative_and_explicit_dates_must_agree_and_unattached_values_are_ambiguous(self):
        for message in (
            "ngày mai 10/10/2026 lúc 23:00",
            "10/10/2026 23:00 21:00",
        ):
            with self.subTest(message=message):
                with self.assertRaises(DomainError):
                    self._parse(message)

    def test_period_words_resolve_evening_hours_without_guessing_a_different_hour(self):
        self._assert_one_date("11 giờ tối 10/10/2026", datetime(2026, 10, 10, 23, 0))
        self._assert_one_date("10/10 11 giờ tối", datetime(2026, 10, 10, 23, 0))
        self._assert_one_date("tối 23:00 10/10/2026", datetime(2026, 10, 10, 23, 0))
        self._assert_one_date("10/10 đêm 1h", datetime(2026, 10, 10, 1, 0))
        for message in ("7 giờ sáng tối 10/10/2026", "10/10 chiều 0h", "10/10 tối 1h"):
            with self.subTest(message=message), self.assertRaises(DomainError):
                self._parse(message)

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
        g.pop("_login_user", None)

    def csrf(self):
        self.client.get("/ai")
        return csrf_for(self.client)

    def _assistant(self, question, conversation_id, csrf=True):
        headers = {"X-CSRF-Token": self.csrf()} if csrf else None
        return self.client.post("/api/v1/assistant", json={
            "question": question,
            "conversation_id": conversation_id,
        }, headers=headers)

    def test_task_deadline_followup_keeps_context_and_stays_pending_for_review(self):
        self.login(1)
        csrf_token = self.csrf()
        conversation_response = self.client.post(
            "/api/v1/assistant/conversations",
            json={"title": "Task deadline clarification"},
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(conversation_response.status_code, 201, conversation_response.json)
        conversation_id = conversation_response.json["id"]

        first_request = (
            "Tạo task Tuyển NY cho Vương Phan thuộc ban Truyền thông "
            "trong sự kiện Workshop tuyển thành viên"
        )
        blocked_first = self._assistant(first_request, conversation_id, csrf=False)
        self.assertEqual(blocked_first.status_code, 400, blocked_first.json)
        self.assertEqual(ChatMessage.query.count(), 0)

        with self._fixed_clock(), patch(
            "app.services.assistant.urlopen",
            side_effect=AssertionError("natural proposals must not call a model"),
        ):
            first = self._assistant(first_request, conversation_id)
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(first.json["conversation_id"], conversation_id)
        self.assertEqual(first.json["intent"], "proposal_needs_details")
        self.assertTrue(first.json["preview"])
        pending = ConversationPending.query.filter_by(conversation_id=conversation_id).one()
        self.assertEqual(pending.kind, "task")
        self.assertIn("Vương Phan", pending.text)
        self.assertIsNone(ConversationPending.query.filter(
            ConversationPending.conversation_id != conversation_id,
        ).first())
        self.assertIsNone(AssistantPending.query.filter_by(user_id=1).first())
        self.assertEqual(AIProposal.query.count(), 0)
        self.assertEqual(Task.query.count(), 0)

        followup = "Tuyển NY cho Vương Phan, hạn là tối 23:00 10/10"
        blocked_followup = self._assistant(followup, conversation_id, csrf=False)
        self.assertEqual(blocked_followup.status_code, 400, blocked_followup.json)
        db.session.refresh(pending)
        self.assertEqual(AIProposal.query.count(), 0)
        self.assertEqual(Task.query.count(), 0)

        # Simulate the pre-upgrade state reported by the user: the old parser
        # already appended this exact follow-up to the conversation's pending
        # request and asked for clarification. Re-sending it after the parser
        # upgrade must not require cancelling the pending request first.
        previous_clarification = "Chưa nhận diện được thời hạn; vui lòng nêu ngày và giờ."
        pending.text = f"{pending.text} {followup}"
        db.session.add_all([
            ChatMessage(user_id=1, conversation_id=conversation_id, role="user",
                        content=followup),
            ChatMessage(user_id=1, conversation_id=conversation_id, role="ai",
                        content=previous_clarification),
        ])
        db.session.commit()
        self.assertIn(followup, pending.text)

        with self._fixed_clock(), patch(
            "app.services.assistant.urlopen",
            side_effect=AssertionError("natural proposals must not call a model"),
        ):
            completed = self._assistant(followup, conversation_id)
        self.assertEqual(completed.status_code, 200, completed.json)
        self.assertEqual(completed.json["conversation_id"], conversation_id)
        self.assertEqual(completed.json["intent"], "ai_proposal")
        self.assertTrue(completed.json["preview"])
        self.assertIn("10/10/2026", completed.json["answer"])

        proposal = db.session.get(AIProposal, completed.json["proposal_id"])
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.status, "pending")
        self.assertEqual(proposal.kind, "task")
        self.assertEqual(proposal.payload["deadline"], "2026-10-10T23:00:00")
        self.assertEqual(proposal.payload["event_id"], 1)
        self.assertEqual(proposal.payload["ban_id"], 1)
        self.assertEqual(len(proposal.payload["tasks"]), 1)
        self.assertEqual(proposal.payload["tasks"][0]["name"], "Tuyển NY")
        self.assertEqual(proposal.payload["tasks"][0]["assignee_id"], 2)
        pending_proposals = self.client.get("/api/v1/assistant/proposals?status=pending")
        self.assertEqual(pending_proposals.status_code, 200, pending_proposals.json)
        visible_proposal = next(
            row for row in pending_proposals.json["items"] if row["id"] == proposal.id
        )
        self.assertEqual(visible_proposal["status"], "pending")
        self.assertIn("2026-10-10T23:00:00", visible_proposal["preview"])
        self.assertIsNone(ConversationPending.query.filter_by(conversation_id=conversation_id).first())
        self.assertEqual(Task.query.count(), 0, "a pending proposal must not create a task")

        messages = ChatMessage.query.filter_by(conversation_id=conversation_id).order_by(
            ChatMessage.id,
        ).all()
        self.assertEqual([message.content for message in messages], [
            first_request,
            first.json["answer"],
            followup,
            previous_clarification,
            followup,
            completed.json["answer"],
        ])

    def test_task_with_two_distinct_deadlines_stays_in_clarification(self):
        self.login(1)
        csrf_token = self.csrf()
        conversation_response = self.client.post(
            "/api/v1/assistant/conversations",
            json={"title": "Conflicting task deadlines"},
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(conversation_response.status_code, 201, conversation_response.json)
        conversation_id = conversation_response.json["id"]
        db.session.add(ConversationPending(
            user_id=1,
            conversation_id=conversation_id,
            kind="task",
            text=(
                "Tạo task Tuyển NY cho Vương Phan thuộc ban Truyền thông "
                "trong sự kiện Workshop tuyển thành viên, hạn 23:00 ngày 10/10/2026"
            ),
        ))
        db.session.commit()

        with self._fixed_clock(), patch(
            "app.services.assistant.urlopen",
            side_effect=AssertionError("natural proposals must not call a model"),
        ):
            response = self._assistant("Hạn 22:00 ngày 11/10/2026", conversation_id)

        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["intent"], "proposal_needs_details")
        self.assertIn("nhiều ngày/giờ", response.json["answer"])
        pending = ConversationPending.query.filter_by(conversation_id=conversation_id).one()
        self.assertIn("23:00 ngày 10/10/2026", pending.text)
        self.assertIn("22:00 ngày 11/10/2026", pending.text)
        self.assertEqual(AIProposal.query.count(), 0)
        self.assertEqual(Task.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
