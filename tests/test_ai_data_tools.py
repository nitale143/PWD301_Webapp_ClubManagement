"""Công cụ đọc dữ liệu có tham số (club_data_tools) + bước viết câu trả lời có đối chiếu số."""

import json
import tempfile
import unittest
from collections import deque
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from app import create_app, db
from app.models import (
    Attendance, Ban, Event, EventRegistration, FundPeriod, MemberRecord, User, UserBan,
)
from app.services.activities import refresh_period
from app.services.agent import respond_events
from app.services.assistant import RulesProvider
from app.services.club_data_tools import answer_by_rules, detect_tool, merge_args
from app.services.common import DomainError, club_now


class FakeModel:
    """Model giả: trả lần lượt các JSON đã định sẵn, ghi lại mọi lượt gọi."""

    def __init__(self, responses):
        self.responses = deque(responses)
        self.calls = []

    def complete(self, system, prompt, **kwargs):
        self.calls.append((kwargs.get("purpose"), prompt))
        if not self.responses:
            raise AssertionError("gọi model ngoài dự kiến")
        value = self.responses.popleft()
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    def answer(self, actor, question, history=None):
        return RulesProvider().answer(actor, question)


def plan(tool, query="", **args):
    data = {"action": "tool", "tool": tool, "query": query}
    if args:
        data["args"] = args
    return data


class DataToolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / "data-tools.db"
        self.app = create_app({
            "TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}",
            "SECRET_KEY": "test-only-key", "AI_PROVIDER": "rules",
            "AI_AGENT_ENABLED": True, "AI_AGENT_MAX_STEPS": 3, "AI_AGENT_MAX_SECONDS": 10,
            "AI_CHAT_MESSAGES_PER_MINUTE": 100,
            "AI_AGENT_FAST_FINISH": True, "AI_AGENT_COMPOSE": False,
        })
        self.context = self.app.test_request_context()
        self.context.push()
        db.session.add_all([Ban(id=1, ten_ban="Kỹ thuật"), Ban(id=2, ten_ban="Truyền thông")])
        for user_id, code, name, role, ban in (
                (1, "SE180001", "Casey Chair", "CN", 1),
                (2, "SE180002", "Taylor Lead", "TB", 1),
                (3, "SE180003", "Morgan Own", "THANH_VIEN", 1),
                (4, "SE180004", "Riley Outside", "THANH_VIEN", 2)):
            user = User(id=user_id, mssv=code, ho_ten=name, ngay_sinh=date(2004, 1, 1),
                        sdt=f"+8490000000{user_id}", email=f"m{user_id}@example.com",
                        status="approved", chuc_vu=role)
            user.set_password("test-password")
            db.session.add(user)
            db.session.add(MemberRecord(user_id=user_id, ngay_gia_nhap=date(2025, 1, 1)))
            db.session.add(UserBan(user_id=user_id, ban_id=ban))
        now = club_now()
        past = Event(id=10, ten_su_kien="Orientation", ma_su_kien="ORI26",
                     thoi_gian_bat_dau=now - timedelta(days=2, hours=3),
                     thoi_gian_ket_thuc=now - timedelta(days=2), trang_thai="da_ket_thuc", tao_boi_id=1)
        soon = Event(id=11, ten_su_kien="Gala mùa thu", ma_su_kien="GALA",
                     thoi_gian_bat_dau=now + timedelta(days=5),
                     thoi_gian_ket_thuc=now + timedelta(days=5, hours=3), trang_thai="sap_dien_ra",
                     tao_boi_id=1)
        db.session.add_all([past, soon])
        db.session.flush()
        for user_id, state in ((1, "on_time"), (2, "absent"), (3, "on_time"), (4, "excused")):
            reg = EventRegistration(event_id=10, user_id=user_id, trang_thai="registered")
            db.session.add(reg)
            db.session.flush()
            db.session.add(Attendance(registration_id=reg.id, trang_thai=state, checkin_boi_id=1,
                                      checkin_luc=club_now() - timedelta(days=2) if state == "on_time" else None))
        for user_id, state in ((1, "registered"), (2, "cancelled"), (3, "registered"), (4, "registered")):
            db.session.add(EventRegistration(event_id=11, user_id=user_id, trang_thai=state))
        period = FundPeriod(ten_ky="Fall 2026", ngay_bat_dau=(now - timedelta(days=30)).date(),
                            ngay_ket_thuc=(now + timedelta(days=60)).date(), is_current=True)
        db.session.add(period)
        db.session.flush()
        # Không có task: điểm = tỉ lệ có mặt. 1, 3 có mặt → Tích cực; 2 vắng → Không hoạt động;
        # 4 vắng có phép (không tính) → Chưa xếp loại.
        refresh_period(period)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.context.pop()
        self.directory.cleanup()

    def user(self, user_id):
        return db.session.get(User, user_id)

    def run_agent(self, question, model, actor_id=1):
        with patch("app.services.agent_orchestrator.get_provider", return_value=model):
            events = list(respond_events(self.user(actor_id), question))
        results = [event["result"] for event in events if event.get("type") == "result"]
        self.assertEqual(len(results), 1, events)
        return results[0]

    # ------------------------------------------------------------ luật (không model)
    def test_member_question_about_upcoming_event_counts_people_not_lists_events(self):
        """Lỗi cũ: hỏi thành viên trong sự kiện sắp diễn ra lại trả về danh sách sự kiện."""
        question = "có bao nhiêu thành viên trong sự kiện sắp diễn ra"
        self.assertEqual(detect_tool(question), "event_participation")
        result = RulesProvider().answer(self.user(1), question)
        self.assertEqual(result["intent"], "event_participation")
        self.assertIn("Gala mùa thu", result["answer"])
        self.assertIn("Đăng ký (chưa hủy): 3 người", result["answer"])
        self.assertNotIn("Sự kiện sắp diễn ra:", result["answer"])

    def test_plain_upcoming_event_list_keeps_old_reader(self):
        result = RulesProvider().answer(self.user(1), "Liệt kê sự kiện sắp diễn ra")
        self.assertEqual(result["intent"], "upcoming_events")

    def test_active_member_count_uses_club_classification(self):
        result = RulesProvider().answer(self.user(1), "có bao nhiêu thành viên tích cực")
        self.assertEqual(result["intent"], "member_stats")
        self.assertIn("Số thành viên (mức Tích cực): 2.", result["answer"])
        self.assertIn("Casey Chair", result["answer"])
        self.assertIn("Morgan Own", result["answer"])
        self.assertNotIn("Riley Outside", result["answer"])
        self.assertIn("Cách xếp loại: điểm = 50% tỉ lệ task", result["answer"])

    def test_ban_filter_and_absent_list_of_latest_event(self):
        ban = answer_by_rules(self.user(1), "Ban Kỹ thuật có bao nhiêu thành viên")
        self.assertIn("Số thành viên (ban Kỹ thuật): 3.", ban["answer"])
        absent = answer_by_rules(self.user(1), "có bao nhiêu thành viên vắng mặt trong sự kiện gần nhất")
        self.assertIn("Orientation", absent["answer"])
        self.assertIn("vắng: 1", absent["answer"])
        self.assertIn("Người vắng (1):", absent["answer"])
        self.assertIn("Taylor Lead", absent["answer"])

    def test_ordinary_member_sees_directory_counts_and_own_status_only(self):
        member = self.user(3)
        self.assertIn("Số thành viên (ban Kỹ thuật): 3.",
                      answer_by_rules(member, "ban Kỹ thuật có bao nhiêu thành viên")["answer"])
        with self.assertRaises(DomainError):
            answer_by_rules(member, "có bao nhiêu thành viên tích cực")
        with self.assertRaises(DomainError):
            answer_by_rules(member, "bao nhiêu người đăng ký Gala mùa thu")
        own = answer_by_rules(member, "tôi có tích cực không")
        self.assertEqual(own["intent"], "member_activity")
        self.assertIn("Morgan Own", own["answer"])
        mine = answer_by_rules(member, "tôi có đăng ký Gala mùa thu không")
        self.assertIn("Morgan Own (SE180003): đã đăng ký", mine["answer"])
        # Không thể dùng "tôi" để xem trạng thái của người khác.
        with self.assertRaises(DomainError):
            answer_by_rules(member, "SE180004 có đăng ký Gala mùa thu không")

    def test_time_filters_and_multiple_targets_are_not_silently_dropped(self):
        result = answer_by_rules(self.user(1), "có bao nhiêu thành viên tích cực tháng trước")
        self.assertEqual(result["verification"], "unsupported")
        result = answer_by_rules(self.user(1), "bao nhiêu người đăng ký Gala mùa thu và Orientation")
        self.assertEqual(result["verification"], "clarification")

    def test_model_args_cannot_add_scope_but_can_supply_paraphrased_level(self):
        args = merge_args("có bao nhiêu thành viên", {"ban": "Truyền thông", "activity": ""})
        self.assertEqual(args["ban"], "")  # ban không có trong câu hỏi → bỏ
        args = merge_args("mấy bạn hay đi sự kiện là ai", {"activity": "tich_cuc"})
        self.assertEqual(args["activity"], "tich_cuc")
        args = merge_args("bao nhiêu thành viên ít hoạt động", {"activity": "tich_cuc"})
        self.assertEqual(args["activity"], "it_hoat_dong")  # chữ trong câu thắng gợi ý của model

    # ------------------------------------------------------------ agent + model
    def test_agent_uses_model_selected_tool_with_one_planner_call(self):
        model = FakeModel([plan("member_stats", "mấy bạn hay đi sự kiện là ai",
                                activity="tich_cuc", output="list")])
        result = self.run_agent("mấy bạn hay đi sự kiện là ai", model)
        self.assertEqual(len(model.calls), 1)  # câu một ý: không lập kế hoạch lần 2
        self.assertEqual(result["agent_mode"], "model_tools")
        self.assertIn("Số thành viên (mức Tích cực): 2.", result["answer"])

    def test_agent_rejects_unknown_args_and_falls_back_to_rules(self):
        model = FakeModel([plan("member_stats", "x", sql="DROP TABLE user")])
        result = self.run_agent("có bao nhiêu thành viên tích cực", model)
        self.assertEqual(result["agent_mode"], "rules_fallback")
        self.assertEqual(result["agent_fallback_reason"], "invalid_args")
        self.assertIn("Số thành viên (mức Tích cực): 2.", result["answer"])
        self.assertEqual(User.query.count(), 4)

    def test_compound_question_still_plans_each_part(self):
        model = FakeModel([
            plan("member_stats", "ban Kỹ thuật có bao nhiêu thành viên", ban="Kỹ thuật"),
            plan("event_list", "sự kiện đã diễn ra gần đây", window="past"),
            {"action": "finish", "tool": "none", "query": ""},
        ])
        result = self.run_agent("ban Kỹ thuật có bao nhiêu thành viên và sự kiện đã diễn ra gần đây", model)
        self.assertEqual(len(model.calls), 3)
        self.assertIn("Số thành viên (ban Kỹ thuật): 3.", result["answer"])
        self.assertIn("Orientation", result["answer"])

    def test_composed_answer_is_used_only_when_every_number_is_verified(self):
        self.app.config["AI_AGENT_COMPOSE"] = True
        question = "có bao nhiêu thành viên trong sự kiện sắp diễn ra"
        good = FakeModel([plan("event_participation", question, selector="next"),
                          {"answer": "Gala mùa thu (GALA) hiện có 3 người đăng ký."}])
        result = self.run_agent(question, good)
        self.assertTrue(result.get("composed"), result)
        self.assertTrue(result["answer"].startswith("Gala mùa thu (GALA) hiện có 3 người đăng ký."))
        self.assertIn("Đăng ký (chưa hủy): 3 người", result["verified_answer"])
        self.assertEqual([purpose for purpose, _prompt in good.calls], ["agent_plan", "agent_answer"])

        invented = FakeModel([plan("event_participation", question, selector="next"),
                              {"answer": "Có 12 người đăng ký, tăng 50% so với kỳ trước."}])
        result = self.run_agent(question, invented)
        self.assertFalse(result.get("composed"))
        self.assertEqual(result["compose_rejected"], "compose_unverified_number")
        self.assertIn("Đăng ký (chưa hủy): 3 người", result["answer"])
        self.assertNotIn("12 người", result["answer"])

        fake_code = FakeModel([plan("member_stats", "có bao nhiêu thành viên tích cực", activity="tich_cuc"),
                               {"answer": "Có 2 người: SE999999 và Morgan Own."}])
        result = self.run_agent("có bao nhiêu thành viên tích cực", fake_code)
        self.assertEqual(result["compose_rejected"], "compose_unverified_code")

    def test_compose_failure_or_permission_error_never_leaks_data(self):
        self.app.config["AI_AGENT_COMPOSE"] = True
        model = FakeModel([plan("member_stats", "có bao nhiêu thành viên tích cực", activity="tich_cuc")])
        result = self.run_agent("có bao nhiêu thành viên tích cực", model, actor_id=3)
        self.assertEqual(result["intent"], "permission_denied")
        self.assertNotIn("Casey Chair", result["answer"])
        self.assertEqual(len(model.calls), 1)  # không gọi bước viết câu trả lời khi bị từ chối


if __name__ == "__main__":
    unittest.main()
