import tempfile
import unittest
import json
import sqlite3
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
from flask import g
from PIL import Image

from tests.support import csrf_for
from app import create_app, db
from app.models import AIProposal, AIReportSnapshot, AIUsageLog, ConversationPending, Attendance, Ban, ChatMessage, EmailProposal, EmailTemplate, Event, EventRegistration, EventTargetBan, FundCollection, FundDue, FundPayment, FundPeriod, MemberPlanningProfile, MemberRecord, Task, User, UserBan
from app.legacy_sqlite import migrate_email_recipients, migrate_legacy_money
from app.services.common import DomainError, club_now, parse_datetime
from app.services.briefings import daily_digest, report_data, report_text


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / "club-test.db"
        self.app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}",
                               "SECRET_KEY": "test-only-key",
                               "PAYMENT_EVIDENCE_DIR": str(Path(self.directory.name) / "evidence")})
        self.client = self.app.test_client()
        self.context = self.app.app_context()
        self.context.push()
        db.session.add(Ban(id=1, ten_ban="Ky thuat"))
        for user_id, role in ((1, "CN"), (2, "THUKY_THUQUY"), (3, "THANH_VIEN"), (4, "THANH_VIEN")):
            user = User(id=user_id, mssv=f"SV{user_id}", ho_ten=f"Member {user_id}",
                        ngay_sinh=date(2003, 1, 1), sdt="0123456789",
                        email=f"member{user_id}@example.test", status="approved", chuc_vu=role)
            user.set_password("test-password")
            db.session.add(user)
            db.session.add(MemberRecord(user_id=user_id, ngay_gia_nhap=date(2024, 1, 1)))
            db.session.add(UserBan(user_id=user_id, ban_id=1))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.context.pop()
        self.directory.cleanup()

    def login(self, user_id):
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True
        g.pop("_login_user", None)

    def csrf(self):
        return csrf_for(self.client)

    def csrf_request(self, method, path, *args, **kwargs):
        """Send a browser-style unsafe request with the current session token."""
        token = self.csrf()
        if "json" in kwargs:
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault("X-CSRF-Token", token)
            kwargs["headers"] = headers
        else:
            data = args[0] if args else kwargs.get("data")
            if data is None:
                data = {}
            if isinstance(data, dict):
                data = dict(data)
                data.setdefault("csrf_token", token)
                if args:
                    args = (data, *args[1:])
                else:
                    kwargs["data"] = data
            else:
                headers = dict(kwargs.get("headers") or {})
                headers.setdefault("X-CSRF-Token", token)
                kwargs["headers"] = headers
        return self.client.open(path, *args, method=method, **kwargs)

    def create_fund(self, amount="100.00", due=None):
        self.login(2)
        today = date.today()
        response = self.csrf_request("POST", "/api/v1/funds", json={
            "name": "Quy thang", "type": "semester", "amount": amount,
            "start_date": (today - timedelta(days=30)).isoformat(),
            "due_date": (due or today + timedelta(days=2)).isoformat(),
            "applies_to_all": True, "status": "open"})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json["id"]

    def create_event(self):
        self.login(1)
        start = datetime.utcnow() + timedelta(days=1)
        response = self.csrf_request("POST", "/api/v1/events", json={
            "name": "Workshop", "code": f"WS{Event.query.count() + 1}",
            "start_at": start.isoformat(), "end_at": (start + timedelta(hours=2)).isoformat(),
            "registration_deadline": (start + timedelta(hours=1)).isoformat()})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json["id"]

    def test_payment_balance_overdue_and_permissions(self):
        self.assertEqual(self.client.get("/api/v1/dashboard").status_code, 401)
        fund_id = self.create_fund(due=date.today() - timedelta(days=1))
        self.login(3)
        forbidden = self.csrf_request("POST", "/api/v1/funds", json={})
        self.assertEqual(forbidden.status_code, 403)
        first = self.csrf_request("POST", f"/api/v1/funds/{fund_id}/payments",
                                 json={"amount": "40.00", "method": "transfer"})
        self.assertEqual(first.status_code, 201, first.json)
        image = BytesIO()
        Image.new("RGB", (2, 2), "white").save(image, format="PNG")
        image.seek(0)
        evidence = self.csrf_request("POST", f"/api/v1/payments/{first.json['id']}/evidence",
                                    data={"file": (image, "receipt.png")})
        self.assertEqual(evidence.status_code, 201, evidence.json)
        self.assertTrue(self.client.get(f"/api/v1/payments/evidence/{evidence.json['id']}").data.startswith(b"\x89PNG"))
        duplicate_pending = self.csrf_request("POST", f"/api/v1/funds/{fund_id}/payments",
                                             json={"amount": "70.00", "method": "transfer"})
        self.assertEqual(duplicate_pending.status_code, 400)
        self.login(2)
        confirmed = self.csrf_request("POST", f"/api/v1/payments/{first.json['id']}/review",
                                     json={"decision": "confirmed"})
        self.assertEqual(confirmed.status_code, 200, confirmed.json)
        self.assertEqual(self.csrf_request("POST", f"/api/v1/payments/{first.json['id']}/review",
                                          json={"decision": "confirmed"}).status_code, 400)
        row = self.client.get(f"/api/v1/funds/{fund_id}/balances").json["items"]
        member = next(item for item in row if item["member"]["id"] == 3)
        self.assertEqual(member["balance"]["paid"], "40.00")
        self.assertEqual(member["balance"]["remaining"], "60.00")
        self.assertEqual(member["balance"]["status"], "overdue")
        adjusted = self.csrf_request("PUT", f"/api/v1/funds/{fund_id}/adjustments/3",
                                   json={"type": "reduce", "amount": "20.00", "reason": "Ho tro"})
        self.assertEqual(adjusted.status_code, 200, adjusted.json)
        member = next(item for item in self.client.get(f"/api/v1/funds/{fund_id}/balances").json["items"]
                      if item["member"]["id"] == 3)
        self.assertEqual(member["balance"]["remaining"], "40.00")
        report = self.client.get("/api/v1/reports/funds.xlsx")
        self.assertEqual(report.status_code, 200)
        self.assertTrue(report.data.startswith(b"PK"))

    def test_event_waitlist_promotion_qr_and_activity(self):
        event_id = self.create_event()
        self.login(3)
        first = self.csrf_request("POST", f"/api/v1/events/{event_id}/registrations", json={})
        self.assertEqual(first.json["status"], "registered")
        self.login(4)
        second = self.csrf_request("POST", f"/api/v1/events/{event_id}/registrations", json={})
        self.assertEqual(second.json["status"], "registered")   # không giới hạn số người
        event = db.session.get(Event, event_id)
        event.thoi_gian_bat_dau = club_now() - timedelta(minutes=10)
        event.thoi_gian_ket_thuc = club_now() + timedelta(hours=1)
        db.session.commit()
        self.login(1)
        token = self.client.get(f"/api/v1/events/{event_id}/qr").json["token"]
        png = self.client.get(f"/api/v1/events/{event_id}/qr.png")
        self.assertEqual(png.status_code, 200)
        self.assertTrue(png.data.startswith(b"\x89PNG"))
        self.login(4)
        checkin = self.csrf_request("POST", "/api/v1/attendance/checkin", json={"token": token})
        self.assertEqual(checkin.status_code, 200, checkin.json)
        duplicate = self.csrf_request("POST", "/api/v1/attendance/checkin", json={"token": token})
        self.assertEqual(duplicate.status_code, 400, duplicate.json)
        self.assertEqual(self.client.get("/api/v1/members/4/activity").json["points"], 6)

    def test_assistant_scope_and_member_archive(self):
        fund_id = self.create_fund()
        self.login(3)
        answer = self.csrf_request("POST", "/api/v1/assistant", json={"question": "Ai chưa đóng quỹ tháng này?"})
        self.assertEqual(answer.status_code, 200, answer.json)
        self.assertNotIn("Member 4", answer.json["answer"])
        self.assertEqual(self.csrf_request("POST", "/api/v1/assistant", json={"question": "Soạn thông báo nhắc đóng quỹ"}).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/members/4").status_code, 403)
        self.login(2)
        preview = self.csrf_request("POST", "/api/v1/assistant", json={"question": "Soạn thông báo nhắc đóng quỹ"})
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.json["preview"])
        self.login(1)
        archived = self.csrf_request("POST", "/api/v1/members/4/archive")
        self.assertEqual(archived.status_code, 200, archived.json)
        self.login(4)
        self.assertEqual(self.client.get("/api/v1/dashboard").status_code, 403)

    def test_event_edit_copy_cancel_and_activity_setting(self):
        event_id = self.create_event()
        updated = self.csrf_request("PATCH", f"/api/v1/events/{event_id}", json={"location": "Phong A"})
        self.assertEqual(updated.status_code, 200, updated.json)
        self.assertEqual(updated.json["location"], "Phong A")
        start = datetime.utcnow() + timedelta(days=3)
        copied = self.csrf_request("POST", f"/api/v1/events/{event_id}/copy", json={
            "code": "COPY1", "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=1)).isoformat()})
        self.assertEqual(copied.status_code, 201, copied.json)
        self.assertEqual(copied.json["location"], "Phong A")
        cancelled = self.csrf_request("POST", f"/api/v1/events/{event_id}/cancel")
        self.assertEqual(cancelled.json["status"], "cancelled")
        self.login(3)
        denied = self.csrf_request("PUT", "/api/v1/settings/activity", json={"points": {}})
        self.assertEqual(denied.status_code, 403)

    def test_existing_pages_still_render(self):
        self.login(3)
        for path in ("/member", "/event", "/funds", "/ai", "/profile"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)

    def test_member_import_is_atomic(self):
        self.login(1)
        header = "code,name,birth_date,email,phone,department_ids,password\n"
        good = "se240101,New Member,2003-01-01,new1@example.test,0123456789,1,secret123\n"
        invalid = "bad!,Bad Member,2003-01-01,new2@example.test,0123456789,1,secret123\n"
        rejected = self.csrf_request("POST", "/api/v1/members/import", data={
            "file": (BytesIO((header + good + invalid).encode()), "members.csv")})
        self.assertEqual(rejected.status_code, 400, rejected.json)
        self.assertIsNone(User.query.filter_by(mssv="SE240101").first())
        accepted = self.csrf_request("POST", "/api/v1/members/import", data={
            "file": (BytesIO((header + good).encode()), "members.csv")})
        self.assertEqual(accepted.status_code, 201, accepted.json)
        self.assertEqual(accepted.json["imported"], 1)
        # MSSV được viết hoa, SĐT chuẩn hóa về +84 + 9 số
        self.assertEqual(User.query.filter_by(mssv="SE240101").one().sdt, "+84123456789")

    def test_ai_provider_uses_shared_key_and_scoped_context(self):
        self.app.config.update(AI_PROVIDER="anthropic", AI_API_KEY="test-shared-key", AI_STRICT_FACTS=False)
        self.login(3)
        mock_response = BytesIO(json.dumps({"content": [
            {"type": "text", "text": "Đây là câu trả lời từ mô hình."}
        ]}).encode())
        with patch("app.services.assistant.urlopen", return_value=mock_response) as mocked:
            response = self.csrf_request("POST", "/api/v1/assistant", json={"question": "Hướng dẫn sử dụng CLB"})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["provider"], "anthropic")
        req = mocked.call_args.args[0]
        self.assertEqual(req.get_header("X-api-key"), "test-shared-key")
        sent = json.loads(req.data)
        self.assertIn("Member 3", sent["messages"][0]["content"])
        self.assertNotIn("Member 4", sent["messages"][0]["content"])

    def test_gemini_chat_and_proposal_keep_scoped_context_and_review_gate(self):
        self.app.config.update(AI_PROVIDER="gemini", GEMINI_API_KEY="test-gemini-key",
                               GEMINI_MODEL="gemini-3.8-flash", AI_STRICT_FACTS=False)
        self.login(3)
        fake = BytesIO(json.dumps({"candidates": [{"content": {"parts": [
            {"text": "Thông tin đã được kiểm chứng."}]}}]}).encode())
        with patch("app.services.assistant.urlopen", return_value=fake) as mocked:
            response = self.csrf_request("POST", "/api/v1/assistant", json={"question": "Hướng dẫn sử dụng CLB"})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json["provider"], "gemini")
        req = mocked.call_args.args[0]
        self.assertEqual(req.get_header("X-goog-api-key"), "test-gemini-key")
        self.assertIn("gemini-3.8-flash:generateContent", req.full_url)
        sent = json.loads(req.data)
        self.assertIn("Member 3", sent["contents"][0]["parts"][0]["text"])
        self.assertNotIn("Member 4", sent["contents"][0]["parts"][0]["text"])
        fake_web = BytesIO(json.dumps({"candidates": [{"content": {"parts": [
            {"text": "Trả lời trên trang AI."}]}}]}).encode())
        with patch("app.services.assistant.urlopen", return_value=fake_web):
            web = self.csrf_request("POST", "/ai", data={"message": "Hướng dẫn sử dụng CLB",
                                                "csrf_token": self.csrf()},
                                   follow_redirects=True)
        self.assertEqual(web.status_code, 200)
        self.assertIn("Trả lời trên trang AI.".encode(), web.data)

        self.login(1)
        token = self.csrf()
        start = datetime.utcnow() + timedelta(days=4)
        model_json = json.dumps({"name": "Workshop Gemini", "content": "Học cùng nhau",
                                 "type": "workshop", "location": "Phòng A", "capacity": 25})
        fake = BytesIO(json.dumps({"candidates": [{"content": {"parts": [
            {"text": model_json}]}}]}).encode())
        with patch("app.services.assistant.urlopen", return_value=fake):
            created = self.csrf_request("POST", "/api/v1/assistant/proposals", json={
                "kind": "event", "brief": "Tổ chức workshop",
                "start_at": start.isoformat(),
                "end_at": (start + timedelta(hours=2)).isoformat(),
            }, headers={"X-CSRF-Token": token})
        self.assertEqual(created.status_code, 201, created.json)
        self.assertEqual(created.json["provider"], "gemini")
        self.assertEqual(Event.query.count(), 0)
        reviewed = self.csrf_request("POST", f"/api/v1/assistant/proposals/{created.json['id']}/review",
                                    json={"decision": "approved"}, headers={"X-CSRF-Token": token})
        self.assertEqual(reviewed.status_code, 200, reviewed.json)
        self.assertEqual(Event.query.one().ten_su_kien, "Workshop Gemini")

    def test_gemini_retries_transient_failure_then_uses_verified_fallback(self):
        self.app.config.update(AI_PROVIDER="gemini", GEMINI_API_KEY="test-gemini-key",
                               GEMINI_MODEL="gemini-3.5-flash-lite", AI_STRICT_FACTS=False)
        self.login(3)

        def http_error(code):
            return HTTPError("https://generativelanguage.googleapis.com/", code,
                             "test error", {}, BytesIO(b"{}"))

        success = BytesIO(json.dumps({"candidates": [{"content": {"parts": [
            {"text": "Dữ liệu quỹ đã kiểm chứng."}]}}]}).encode())
        with patch("app.services.assistant.urlopen", side_effect=[http_error(503), success]) as call, \
             patch("app.services.assistant.sleep") as pause:
            retried = self.csrf_request("POST", "/api/v1/assistant", json={"question": "Hướng dẫn sử dụng CLB"})
        self.assertEqual(retried.status_code, 200, retried.json)
        self.assertEqual(retried.json["provider"], "gemini")
        self.assertEqual(call.call_count, 2)
        pause.assert_called_once()

        with patch("app.services.assistant.urlopen", side_effect=[http_error(503), http_error(503)]) as call, \
             patch("app.services.assistant.sleep"):
            fallback = self.csrf_request("POST", "/api/v1/assistant", json={"question": "Hướng dẫn sử dụng CLB"})
        self.assertEqual(fallback.status_code, 200, fallback.json)
        self.assertEqual(fallback.json["provider"], "rules_fallback")
        self.assertEqual(fallback.json["verification"], "unsupported")
        self.assertIn("Gemini đang bận", fallback.json["answer"])
        self.assertEqual(call.call_count, 2)

        with patch("app.services.assistant.urlopen", side_effect=http_error(400)) as call:
            invalid = self.csrf_request("POST", "/api/v1/assistant", json={"question": "Hướng dẫn sử dụng CLB"})
        self.assertEqual(invalid.status_code, 503, invalid.json)
        self.assertIn("Kiểm tra tên model", invalid.json["error"])
        self.assertEqual(call.call_count, 1)

    def test_gemini_structured_proposal_records_tokens_and_limits_calls(self):
        self.app.config.update(AI_PROVIDER="gemini", GEMINI_API_KEY="test-key",
                               GEMINI_MODEL="gemini-3.5-flash-lite",
                               AI_MAX_CALLS_PER_MINUTE=1, AI_STRICT_FACTS=False)
        self.login(1)
        token = self.csrf()
        start = club_now() + timedelta(days=3)
        reply = BytesIO(json.dumps({"candidates": [{"content": {"parts": [{"text":
            json.dumps({"name": "Workshop", "content": "Học tập", "type": "training",
                        "location": "A101", "capacity": 30})}]}}],
            "usageMetadata": {"promptTokenCount": 37, "candidatesTokenCount": 18,
                              "totalTokenCount": 60}}).encode())
        with patch("app.services.assistant.urlopen", return_value=reply) as model:
            created = self.csrf_request("POST", "/api/v1/assistant/proposals", json={
                "kind": "event", "brief": "Workshop học tập",
                "start_at": start.isoformat(),
                "end_at": (start + timedelta(hours=2)).isoformat()},
                headers={"X-CSRF-Token": token})
        self.assertEqual(created.status_code, 201, created.json)
        request_body = json.loads(model.call_args.args[0].data)
        self.assertEqual(request_body["generationConfig"]["responseFormat"]["text"]["mimeType"],
                         "APPLICATION_JSON")
        usage = AIUsageLog.query.one()
        self.assertEqual((usage.purpose, usage.input_tokens, usage.output_tokens),
                         ("proposal", 37, 18))
        self.assertEqual(usage.total_tokens, 60)
        with patch("app.services.assistant.urlopen") as model:
            limited = self.csrf_request("POST", "/api/v1/assistant", json={"question": "Tóm tắt hoạt động"})
        self.assertEqual(limited.status_code, 429, limited.json)
        model.assert_not_called()

    def test_model_usage_is_kept_when_ai_payload_is_invalid(self):
        self.app.config.update(AI_PROVIDER="gemini", GEMINI_API_KEY="test-key")
        self.login(1)
        token = self.csrf()
        start = club_now() + timedelta(days=3)
        bad = BytesIO(json.dumps({"candidates": [{"content": {"parts": [
            {"text": "not-json"}]}}], "usageMetadata": {"totalTokenCount": 42}}).encode())
        with patch("app.services.assistant.urlopen", return_value=bad):
            response = self.csrf_request("POST", "/api/v1/assistant/proposals", json={
                "kind": "event", "brief": "Workshop",
                "start_at": start.isoformat(),
                "end_at": (start + timedelta(hours=2)).isoformat()},
                headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 503, response.json)
        self.assertEqual(AIProposal.query.count(), 0)
        self.assertEqual(AIUsageLog.query.one().total_tokens, 42)

    def test_chat_history_context_and_financial_answers_are_deterministic(self):
        self.app.config.update(AI_PROVIDER="gemini", GEMINI_API_KEY="test-key", AI_STRICT_FACTS=False)
        self.login(1)
        fake_one = BytesIO(json.dumps({"candidates": [{"content": {"parts": [
            {"text": "Thông tin Member 3."}]}}]}).encode())
        with patch("app.services.assistant.urlopen", return_value=fake_one):
            first = self.csrf_request("POST", "/api/v1/assistant", json={
                "question": "Cho tôi biết về Member 3"})
        self.assertEqual(first.status_code, 200, first.json)
        fake_two = BytesIO(json.dumps({"candidates": [{"content": {"parts": [
            {"text": "Chưa có task."}]}}]}).encode())
        with patch("app.services.assistant.urlopen", return_value=fake_two) as model:
            second = self.csrf_request("POST", "/api/v1/assistant", json={
                "question": "Còn người đó có kỹ năng gì?"})
        self.assertEqual(second.status_code, 200, second.json)
        sent = json.loads(model.call_args.args[0].data)
        context = sent["contents"][0]["parts"][0]["text"]
        self.assertIn("Member 3", context)
        self.assertIn("recent_conversation", context)
        self.assertNotIn("Member 4", context)
        with patch("app.services.assistant.urlopen") as model:
            money = self.csrf_request("POST", "/api/v1/assistant", json={"question": "tổng đã thu"})
        self.assertEqual(money.status_code, 200, money.json)
        self.assertEqual(money.json["provider"], "database")
        self.assertIn("0 đ", money.json["answer"])
        model.assert_not_called()

    def test_chat_request_limit_applies_to_api(self):
        self.app.config.update(AI_PROVIDER="rules", AI_CHAT_MESSAGES_PER_MINUTE=1)
        self.login(3)
        first = self.csrf_request("POST", "/api/v1/assistant", json={"question": "hello"})
        self.assertEqual(first.status_code, 200, first.json)
        second = self.csrf_request("POST", "/api/v1/assistant", json={"question": "hello again"})
        self.assertEqual(second.status_code, 429, second.json)

    def test_chat_reports_legacy_period_and_does_not_send_email(self):
        self.app.config.update(AI_PROVIDER="gemini", GEMINI_API_KEY="test-gemini-key")
        period = FundPeriod(ten_ky="Fall 2027", ngay_bat_dau=date.today(), is_current=True)
        db.session.add(period)
        db.session.flush()
        db.session.add_all([
            FundDue(period_id=period.id, user_id=1, da_dong=True),
            FundDue(period_id=period.id, user_id=3, da_dong=True),
            FundDue(period_id=period.id, user_id=4, da_dong=False),
        ])
        db.session.commit()
        self.login(1)
        with patch("app.services.assistant.urlopen") as model, \
             patch("app.mail.smtplib.SMTP") as smtp:
            overview = self.csrf_request("POST", "/api/v1/assistant", json={"question": "kiểm tra quỹ"})
            unpaid = self.csrf_request("POST", "/api/v1/assistant", json={
                "question": "còn ai chưa đóng quỹ kỳ Fall 2027"})
            mail = self.csrf_request("POST", "/api/v1/assistant", json={
                "question": "gửi mail cho Member 4"})
        self.assertEqual(overview.status_code, 200, overview.json)
        self.assertIn("Fall 2027: đã đóng 2/3", overview.json["answer"])
        self.assertIn("Khoản thu mới đang mở: 0", overview.json["answer"])
        self.assertEqual(unpaid.status_code, 200, unpaid.json)
        self.assertIn("Member 4", unpaid.json["answer"])
        self.assertNotIn("Member 3", unpaid.json["answer"])
        self.assertEqual(mail.status_code, 200, mail.json)
        self.assertIn("chưa gửi email", mail.json["answer"])
        model.assert_not_called()
        smtp.assert_not_called()

        self.login(3)
        scoped = self.csrf_request("POST", "/api/v1/assistant", json={
            "question": "còn ai chưa đóng quỹ kỳ Fall 2027"})
        self.assertNotIn("Member 4", scoped.json["answer"])

    def test_email_chat_creates_draft_and_requires_board_review(self):
        self.app.config.update(AI_PROVIDER="rules", MAIL_ENABLED=True)
        recipient = db.session.get(User, 3)
        recipient.email = "member3@club.org"
        db.session.commit()
        self.login(1)
        token = self.csrf()
        self.assertEqual(self.client.post("/ai", data={
            "message": "gửi mail cho Member 3"}).status_code, 400)
        needs_details = self.csrf_request("POST", "/ai", data={
            "message": "gửi mail cho Member 3", "csrf_token": token}, follow_redirects=True)
        self.assertIn("Tôi chưa gửi email".encode(), needs_details.data)
        self.assertEqual(EmailProposal.query.count(), 0)

        with patch("app.services.email_proposals.send_email", return_value=True) as send:
            created = self.csrf_request("POST", "/ai", data={
                "message": "gửi mail cho Member 3: nhắc hoàn thành hồ sơ CLB",
                "csrf_token": token}, follow_redirects=True)
            self.assertEqual(created.status_code, 200)
            draft = EmailProposal.query.one()
            self.assertEqual(draft.status, "pending")
            self.assertEqual(draft.recipient_id, recipient.id)
            self.assertIn("nhắc hoàn thành hồ sơ CLB", draft.body)
            self.assertIn("member3@club.org".encode(), created.data)
            send.assert_not_called()

            self.login(3)
            denied = self.csrf_request("POST", f"/ai/email-proposals/{draft.id}/approved",
                                      data={"csrf_token": token})
            self.assertEqual(denied.status_code, 403)
            self.login(2)
            sent = self.csrf_request("POST", f"/ai/email-proposals/{draft.id}/approved",
                                    data={"csrf_token": token}, follow_redirects=True)
            self.assertEqual(sent.status_code, 200)
            self.assertEqual(db.session.get(EmailProposal, draft.id).status, "sent")
            send.assert_called_once_with("member3@club.org", draft.subject, draft.body)
            self.csrf_request("POST", f"/ai/email-proposals/{draft.id}/approved",
                             data={"csrf_token": token})
            self.assertEqual(send.call_count, 1)

    def test_saved_email_template_natural_request_stays_a_draft(self):
        self.app.config.update(AI_PROVIDER="rules", MAIL_ENABLED=True)
        db.session.get(User, 3).email = "member3@club.org"
        db.session.commit()
        self.login(1)
        token = self.csrf()
        form = {"name": "Nhắc đóng quỹ", "subject": "Nhắc quỹ cho {{ten}}",
                "body": "Chào {{ten}} ({{mssv}}), vui lòng kiểm tra quỹ. Email: {{email}}."}
        self.assertEqual(self.client.post("/kho-mau/email", data=form).status_code, 400)
        saved = self.csrf_request("POST", "/kho-mau/email", data={**form, "csrf_token": token})
        self.assertEqual(saved.status_code, 302)
        template = EmailTemplate.query.one()
        self.assertIn("Nhắc đóng quỹ", self.client.get("/ai").get_data(as_text=True))

        command = "Dùng mẫu nhắc đóng quỹ gửi cho Member 3 nhé"
        self.assertEqual(self.client.post("/api/v1/assistant", json={"question": command}).status_code, 400)
        with patch("app.services.email_proposals.send_email") as mail:
            created = self.csrf_request("POST", "/api/v1/assistant", json={"question": command},
                                       headers={"X-CSRF-Token": token})
            self.assertEqual(created.status_code, 200, created.json)
            self.assertEqual(created.json["intent"], "email_draft")
            mail.assert_not_called()
        draft = EmailProposal.query.one()
        self.assertEqual(draft.status, "pending")
        self.assertEqual(draft.recipient_id, 3)
        self.assertEqual(draft.subject, "Nhắc quỹ cho Member 3")
        self.assertIn("Member 3 (SV3)", draft.body)
        self.assertIn("member3@club.org", draft.body)

        updated = self.csrf_request("POST", f"/kho-mau/email/{template.id}", data={
            "name": "Nhắc đóng quỹ", "subject": "Tiêu đề mới", "body": "Nội dung mới",
            "csrf_token": token})
        self.assertEqual(updated.status_code, 302)
        self.assertEqual(db.session.get(EmailProposal, draft.id).subject, "Nhắc quỹ cho Member 3")
        with patch("app.services.email_proposals.send_email", return_value=True) as mail:
            approved = self.csrf_request("POST", f"/ai/email-proposals/{draft.id}/approved",
                                        data={"csrf_token": token})
        self.assertEqual(approved.status_code, 302)
        mail.assert_called_once_with("member3@club.org", draft.subject, draft.body)

    def test_email_template_permissions_missing_details_and_deactivation(self):
        self.app.config.update(AI_PROVIDER="rules")
        self.login(1)
        token = self.csrf()
        invalid = self.csrf_request("POST", "/kho-mau/email", data={
            "name": "Mẫu lỗi", "subject": "Chào {{user.password}}", "body": "Nội dung",
            "csrf_token": token}, follow_redirects=True)
        self.assertIn("Chỉ dùng các biến".encode(), invalid.data)
        self.assertEqual(EmailTemplate.query.count(), 0)
        self.csrf_request("POST", "/kho-mau/email", data={
            "name": "Họp CLB", "subject": "Mời họp", "body": "Chào {{ten}}, mời bạn dự họp.",
            "csrf_token": token})
        template = EmailTemplate.query.one()

        self.login(3)
        self.assertEqual(self.csrf_request("POST", "/kho-mau/email", data={
            "name": "Khác", "subject": "A", "body": "B", "csrf_token": token}).status_code, 403)
        self.login(1)
        missing = self.csrf_request("POST", "/api/v1/assistant", json={
            "question": "Gửi mail mẫu họp CLB giúp tôi"}, headers={"X-CSRF-Token": token})
        self.assertEqual(missing.status_code, 200, missing.json)
        self.assertEqual(missing.json["intent"], "email_needs_recipient")
        self.assertEqual(EmailProposal.query.count(), 0)
        unknown = self.csrf_request("POST", "/api/v1/assistant", json={
            "question": "Dùng mẫu không tồn tại gửi cho Member 3"})
        self.assertEqual(unknown.status_code, 200, unknown.json)
        self.assertEqual(unknown.json["intent"], "email_template_needed")
        self.assertEqual(EmailProposal.query.count(), 0)
        self.assertEqual(self.client.post(f"/kho-mau/email/{template.id}/active",
                                          data={"active": "no"}).status_code, 400)
        self.csrf_request("POST", f"/kho-mau/email/{template.id}/active",
                         data={"active": "no", "csrf_token": token})
        self.assertFalse(db.session.get(EmailTemplate, template.id).active)
        result = self.csrf_request("POST", "/api/v1/assistant", json={
            "question": "Gửi mail mẫu họp CLB cho Member 3"})
        self.assertEqual(result.json["intent"], "email_template_needed")
        self.assertEqual(EmailProposal.query.count(), 0)

    def test_email_template_member_scope_and_ambiguous_names(self):
        self.app.config.update(AI_PROVIDER="rules")
        self.login(1)
        token = self.csrf()
        self.csrf_request("POST", "/kho-mau/email", data={
            "name": "Nhắc họp", "subject": "Nhắc họp", "body": "Chào {{ten}}",
            "csrf_token": token})
        db.session.get(User, 3).ho_ten = "Phan Thiện Quáng"
        db.session.get(User, 4).ho_ten = "Phan Thien Quang"
        db.session.commit()
        ambiguous = self.csrf_request("POST", "/api/v1/assistant", json={
            "question": "Gửi mail nhắc họp cho Phan Thien Quang"},
            headers={"X-CSRF-Token": token})
        self.assertEqual(ambiguous.status_code, 400, ambiguous.json)
        self.assertEqual(EmailProposal.query.count(), 0)
        exact = self.csrf_request("POST", "/api/v1/assistant", json={
            "question": "Gửi mail nhắc họp cho Phan Thiện Quáng"},
            headers={"X-CSRF-Token": token})
        self.assertEqual(exact.status_code, 200, exact.json)
        self.assertEqual(EmailProposal.query.one().recipient_id, 3)

    def test_email_template_accepts_short_natural_phrase(self):
        self.app.config.update(AI_PROVIDER="rules")
        self.login(1)
        token = self.csrf()
        self.csrf_request("POST", "/kho-mau/email", data={
            "name": "Nhắc đóng quỹ", "subject": "Nhắc quỹ", "body": "Chào {{ten}}",
            "csrf_token": token})
        message = "AI ơi, gửi mail nhắc quỹ cho Member 3 giúp tôi"
        with patch("app.services.email_proposals.send_email") as send:
            result = self.csrf_request("POST", "/api/v1/assistant", json={"question": message},
                                      headers={"X-CSRF-Token": token})
            send.assert_not_called()
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json["intent"], "email_draft")
        self.assertEqual(EmailProposal.query.one().body, "Chào Member 3")

    def test_email_draft_failure_can_be_retried_or_rejected(self):
        self.app.config.update(AI_PROVIDER="rules", MAIL_ENABLED=True)
        db.session.get(User, 3).email = "member3@club.org"
        db.session.commit()
        self.login(1)
        token = self.csrf()
        self.csrf_request("POST", "/ai", data={"message": "gửi mail cho Member 3: nhắc cập nhật hồ sơ",
                                      "csrf_token": token})
        draft = EmailProposal.query.one()
        with patch("app.services.email_proposals.send_email", return_value=False):
            failed = self.csrf_request("POST", f"/ai/email-proposals/{draft.id}/approved",
                                      data={"csrf_token": token}, follow_redirects=True)
        self.assertEqual(db.session.get(EmailProposal, draft.id).status, "failed")
        self.assertIn("Thử gửi lại".encode(), failed.data)
        with patch("app.services.email_proposals.send_email", return_value=True) as send:
            blocked = self.csrf_request("POST", f"/ai/email-proposals/{draft.id}/approved",
                                       data={"csrf_token": token}, follow_redirects=True)
            self.assertIn("kiểm tra hộp thư".encode(), blocked.data)
            send.assert_not_called()
            self.csrf_request("POST", f"/ai/email-proposals/{draft.id}/approved",
                             data={"csrf_token": token, "confirm_retry": "yes"})
        self.assertEqual(db.session.get(EmailProposal, draft.id).status, "sent")
        send.assert_called_once()

        self.csrf_request("POST", "/ai", data={"message": "gửi mail cho Member 3: họp câu lạc bộ",
                                      "csrf_token": token})
        other = EmailProposal.query.filter_by(status="pending").one()
        with patch("app.services.email_proposals.send_email") as send:
            self.csrf_request("POST", f"/ai/email-proposals/{other.id}/rejected",
                             data={"csrf_token": token})
        self.assertEqual(db.session.get(EmailProposal, other.id).status, "rejected")
        send.assert_not_called()

    def test_email_recipient_with_similar_names_requires_exact_name_or_code(self):
        from app.services.email_proposals import _recipient
        db.session.get(User, 3).ho_ten = "Phan Thiện Quáng"
        db.session.get(User, 4).ho_ten = "Phan Thien Quang"
        db.session.commit()
        actor = db.session.get(User, 1)
        self.assertEqual(_recipient(actor, "Phan Thiện Quáng").id, 3)
        self.assertEqual(_recipient(actor, "SV4").id, 4)
        with self.assertRaises(DomainError):
            _recipient(actor, "Phan Thien Quang")

    def test_gemini_can_write_email_draft_without_sending(self):
        self.app.config.update(AI_PROVIDER="gemini", GEMINI_API_KEY="test-gemini-key")
        self.login(1)
        token = self.csrf()
        draft_json = json.dumps({"subject": "Mời họp CLB", "body": "Chào Member 3,\nMời bạn họp CLB."})
        response = BytesIO(json.dumps({"candidates": [{"content": {"parts": [
            {"text": draft_json}]}}]}).encode())
        with patch("app.services.assistant.urlopen", return_value=response) as model, \
             patch("app.services.email_proposals.send_email") as send:
            created = self.csrf_request("POST", "/ai", data={
                "message": "gửi mail cho SV3: mời họp CLB", "csrf_token": token})
        self.assertEqual(created.status_code, 302)
        draft = EmailProposal.query.one()
        self.assertEqual(draft.subject, "Mời họp CLB")
        self.assertEqual(draft.status, "pending")
        model.assert_called_once()
        send.assert_not_called()

    def test_external_email_address_creates_draft_then_bdh_sends(self):
        self.app.config.update(AI_PROVIDER="rules", MAIL_ENABLED=True)
        self.login(1)
        token = self.csrf()
        with patch("app.services.email_proposals.send_email", return_value=True) as send:
            created = self.csrf_request("POST", "/ai", data={
                "message": "Gửi mail external@partner.org: Liên hệ book phòng",
                "csrf_token": token}, follow_redirects=True)
            self.assertEqual(created.status_code, 200)
            draft = EmailProposal.query.one()
            self.assertIsNone(draft.recipient_id)
            self.assertEqual(draft.recipient_email, "external@partner.org")
            self.assertEqual(draft.status, "pending")
            self.assertIn("Liên hệ book phòng", draft.body)
            self.assertIn(b"external@partner.org", created.data)
            send.assert_not_called()

            self.assertEqual(self.client.post(
                f"/ai/email-proposals/{draft.id}/approved").status_code, 400)
            self.login(3)
            self.assertEqual(self.csrf_request("POST",
                f"/ai/email-proposals/{draft.id}/approved",
                data={"csrf_token": token}).status_code, 403)
            self.login(1)
            approved_response = self.csrf_request("POST",
                f"/ai/email-proposals/{draft.id}/approved",
                data={"csrf_token": token})
            self.assertEqual(approved_response.status_code, 302)
            self.assertEqual(db.session.get(EmailProposal, draft.id).status, "sent")
            send.assert_called_once_with("external@partner.org", draft.subject, draft.body)

    def test_external_email_rejects_invalid_address_and_allows_truong_ban_draft(self):
        self.app.config.update(AI_PROVIDER="rules")
        self.login(1)
        token = self.csrf()
        invalid = self.csrf_request("POST", "/ai", data={
            "message": "Gửi mail bad@@partner.org: Liên hệ book phòng",
            "csrf_token": token}, follow_redirects=True)
        self.assertIn("Địa chỉ email người nhận không hợp lệ".encode(), invalid.data)
        self.assertEqual(EmailProposal.query.count(), 0)
        member = db.session.get(User, 3)
        member.chuc_vu = "TB"
        member.ban_links[0].is_truong_ban = True
        db.session.commit()
        self.login(3)
        drafted = self.csrf_request("POST", "/ai", data={
            "message": "Gửi mail external@partner.org: Liên hệ book phòng",
            "csrf_token": token}, follow_redirects=True)
        self.assertEqual(drafted.status_code, 200, drafted.data)
        proposal = EmailProposal.query.one()
        self.assertEqual(proposal.status, "pending")
        self.assertEqual(proposal.recipient_email, "external@partner.org")

    def test_browser_and_api_share_email_draft_and_chat_history(self):
        self.app.config.update(AI_PROVIDER="rules")
        self.login(1)
        token = self.csrf()
        command = "Gửi mail contact@partner.org: Liên hệ book phòng"
        missing = self.client.post("/api/v1/assistant", json={"question": command})
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(EmailProposal.query.count(), 0)
        result = self.csrf_request("POST", "/api/v1/assistant", json={"question": command},
                                  headers={"X-CSRF-Token": token})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json["intent"], "email_draft")
        self.assertEqual(EmailProposal.query.one().status, "pending")
        self.assertEqual(ChatMessage.query.filter_by(user_id=1).count(), 2)
        self.assertIn(b"contact@partner.org", self.client.get("/ai").data)

    def test_chat_event_proposal_requires_csrf_and_review(self):
        self.app.config.update(AI_PROVIDER="rules")
        self.login(1)
        token = self.csrf()
        start = club_now() + timedelta(days=4)
        command = (f"Đề xuất sự kiện: Workshop an toàn | {start:%Y-%m-%d %H:%M} | "
                   f"{start + timedelta(hours=2):%Y-%m-%d %H:%M}")
        denied = self.client.post("/api/v1/assistant", json={"question": command})
        self.assertEqual(denied.status_code, 400)
        created = self.csrf_request("POST", "/api/v1/assistant", json={"question": command},
                                   headers={"X-CSRF-Token": token})
        self.assertEqual(created.status_code, 200, created.json)
        self.assertEqual(created.json["intent"], "ai_proposal")
        self.assertEqual(Event.query.count(), 0)
        proposal = AIProposal.query.one()
        self.assertIn("Loại: other", self.client.get("/ai").get_data(as_text=True))
        self.csrf_request("POST", f"/ai/proposals/{proposal.id}/approved", data={"csrf_token": token})
        self.assertEqual(Event.query.count(), 1)
        event = Event.query.one()
        task_command = (f"Chia task: {event.id} | 1 | "
                        f"{start + timedelta(hours=1):%Y-%m-%d %H:%M} | "
                        "Chuẩn bị tài liệu; Kiểm tra thiết bị | python")
        task_draft = self.csrf_request("POST", "/api/v1/assistant", json={"question": task_command},
                                      headers={"X-CSRF-Token": token})
        self.assertEqual(task_draft.status_code, 200, task_draft.json)
        self.assertEqual(Task.query.count(), 0)
        task_proposal = AIProposal.query.filter_by(kind="task").one()
        self.csrf_request("POST", f"/ai/proposals/{task_proposal.id}/approved",
                         data={"csrf_token": token})
        self.assertEqual(Task.query.count(), 2)

    def test_member_task_preferences_and_balanced_assignment(self):
        self.app.config.update(AI_PROVIDER="rules")
        self.login(3)
        token = self.csrf()
        saved = self.csrf_request("POST", "/profile/task-preferences", data={
            "skills": "python, bảo mật", "max_active_tasks": "1", "csrf_token": token})
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(db.session.get(MemberPlanningProfile, 3).skills,
                         ["python", "bảo mật"])
        start = club_now() + timedelta(days=3)
        db.session.add(Event(ten_su_kien="Workshop", ma_su_kien="WK001",
                             thoi_gian_bat_dau=start,
                             thoi_gian_ket_thuc=start + timedelta(hours=3),
                             trang_thai="sap_dien_ra", tao_boi_id=1))
        db.session.commit()
        self.login(1)
        event = Event.query.one()
        proposed = self.csrf_request("POST", "/api/v1/assistant/proposals", json={
            "kind": "task", "event_id": event.id, "ban_id": 1,
            "deadline": (start + timedelta(hours=1)).isoformat(),
            "brief": "Viết script Python; Chuẩn bị poster", "required_skills": "python"},
            headers={"X-CSRF-Token": token})
        self.assertEqual(proposed.status_code, 201, proposed.json)
        proposal = AIProposal.query.one()
        assignees = [row["assignee_id"] for row in proposal.payload["tasks"]]
        self.assertEqual(assignees[0], 3)
        self.assertNotEqual(assignees[1], 3)
        self.csrf_request("POST", f"/ai/proposals/{proposal.id}/approved", data={"csrf_token": token})
        self.assertEqual(Task.query.count(), 2)

    def test_task_review_rechecks_member_capacity_and_unavailability(self):
        self.app.config.update(AI_PROVIDER="rules")
        self.login(1)
        token = self.csrf()
        start = club_now() + timedelta(days=3)
        event = Event(ten_su_kien="Workshop", ma_su_kien="WK002",
                      thoi_gian_bat_dau=start,
                      thoi_gian_ket_thuc=start + timedelta(hours=3),
                      trang_thai="sap_dien_ra", tao_boi_id=1)
        db.session.add(event)
        db.session.commit()
        # Others are unavailable, so the first proposal selects member 3.
        db.session.add(MemberPlanningProfile(user_id=1,
                                             unavailable_until=start.date()))
        db.session.add(MemberPlanningProfile(user_id=2,
                                             unavailable_until=start.date()))
        db.session.add(MemberPlanningProfile(user_id=4,
                                             unavailable_until=start.date()))
        db.session.add(MemberPlanningProfile(user_id=3, max_active_tasks=1,
                                             skills=["python"]))
        db.session.commit()
        proposed = self.csrf_request("POST", "/api/v1/assistant/proposals", json={
            "kind": "task", "event_id": event.id, "ban_id": 1,
            "deadline": (start + timedelta(hours=1)).isoformat(),
            "brief": "Viết script Python", "required_skills": "python"},
            headers={"X-CSRF-Token": token})
        self.assertEqual(proposed.status_code, 201, proposed.json)
        self.assertEqual(AIProposal.query.one().payload["tasks"][0]["assignee_id"], 3)
        db.session.get(MemberPlanningProfile, 3).unavailable_until = start.date()
        db.session.commit()
        rejected = self.csrf_request("POST", f"/api/v1/assistant/proposals/{AIProposal.query.one().id}/review",
                                    json={"decision": "approved"},
                                    headers={"X-CSRF-Token": token})
        self.assertEqual(rejected.status_code, 400, rejected.json)
        self.assertEqual(Task.query.count(), 0)

    def test_monthly_report_is_global_for_truong_ban(self):
        self.app.config.update(AI_PROVIDER="rules")
        db.session.add(Ban(id=2, ten_ban="Nhân sự"))
        leader = db.session.get(User, 3)
        leader.chuc_vu = "TB"
        leader.ban_links[0].is_truong_ban = True
        outsider = db.session.get(User, 4)
        outsider.ban_links[0].ban_id = 2
        start = club_now() - timedelta(days=1)
        event = Event(ten_su_kien="Sự kiện ban khác", ma_su_kien="OTHER",
                      thoi_gian_bat_dau=start,
                      thoi_gian_ket_thuc=start + timedelta(hours=1),
                      trang_thai="da_ket_thuc", tao_boi_id=1)
        db.session.add(event)
        db.session.flush()
        reg = EventRegistration(event_id=event.id, user_id=4, trang_thai="registered")
        db.session.add(reg)
        db.session.flush()
        db.session.add(Attendance(registration_id=reg.id, trang_thai="on_time",
                                  checkin_luc=datetime.utcnow() - timedelta(days=1)))
        db.session.commit()
        self.login(3)
        result = self.csrf_request("POST", "/api/v1/assistant", json={"question": "báo cáo tháng này"})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertIn("1 sự kiện, 1 lượt có mặt", result.json["answer"])

    def test_email_edit_and_uncertain_send_need_manual_reconciliation(self):
        self.app.config.update(AI_PROVIDER="rules", MAIL_ENABLED=True)
        self.login(1)
        token = self.csrf()
        self.csrf_request("POST", "/ai", data={"message": "Gửi mail contact@partner.org: book phòng",
                                      "csrf_token": token})
        draft = EmailProposal.query.one()
        edited = self.csrf_request("POST", f"/ai/email-proposals/{draft.id}/edit", data={
            "subject": "Liên hệ đặt phòng", "body": "Chào bạn,\nXin đặt phòng cho CLB.",
            "csrf_token": token})
        self.assertEqual(edited.status_code, 302)
        self.assertEqual(db.session.get(EmailProposal, draft.id).subject, "Liên hệ đặt phòng")
        draft.status = "sending"
        draft.reviewed_at = datetime.utcnow() - timedelta(minutes=3)
        db.session.commit()
        with patch("app.services.email_proposals.send_email") as send:
            reconciled = self.csrf_request("POST",
                f"/ai/email-proposals/{draft.id}/reconcile/failed",
                data={"csrf_token": token})
            self.assertEqual(reconciled.status_code, 302)
            send.assert_not_called()
        self.assertEqual(db.session.get(EmailProposal, draft.id).status, "failed")

    def test_member_approval_sends_email_once_and_requires_board_and_csrf(self):
        period = FundPeriod(ten_ky="Fall Test", ngay_bat_dau=date.today(), is_current=True)
        pending = User(mssv="NEW001", ho_ten="New Member", ngay_sinh=date(2004, 1, 1),
                       sdt="0123456789", email="new@example.test", status="pending",
                       chuc_vu="THANH_VIEN")
        pending.set_password("test-password")
        db.session.add_all([period, pending])
        db.session.flush()
        db.session.add(UserBan(user_id=pending.id, ban_id=1))
        db.session.commit()
        self.login(3)
        self.assertEqual(self.csrf_request("POST", f"/admin/approve/{pending.id}",
                                          data={"action": "accept"}).status_code, 403)
        self.login(1)
        page = self.client.get("/admin/approve")
        self.assertIn(b'name="csrf_token"', page.data)
        self.assertEqual(self.client.post(f"/admin/approve/{pending.id}",
                                          data={"action": "accept"}).status_code, 400)
        with patch("app.routes.admin.send_email", return_value=True) as mail:
            response = self.csrf_request("POST", f"/admin/approve/{pending.id}",
                                        data={"action": "accept", "csrf_token": self.csrf()},
                                        follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(db.session.get(User, pending.id).status, "approved")
        self.assertEqual(db.session.get(User, pending.id).ky_tham_gia_id, period.id)
        self.assertIsNotNone(MemberRecord.query.filter_by(user_id=pending.id).first())
        self.assertIsNotNone(FundDue.query.filter_by(user_id=pending.id, period_id=period.id).first())
        mail.assert_called_once()
        self.assertEqual(mail.call_args.args[0], "new@example.test")
        self.assertIn("duyệt", mail.call_args.args[2])
        with patch("app.routes.admin.send_email") as mail_again:
            self.assertEqual(self.csrf_request("POST", f"/admin/approve/{pending.id}",
                                              data={"action": "accept", "csrf_token": self.csrf()}).status_code, 400)
            mail_again.assert_not_called()

    def test_rejection_email_and_smtp_delivery(self):
        pending = User(mssv="NEW002", ho_ten="Rejected Member", ngay_sinh=date(2004, 1, 1),
                       sdt="0123456789", email="rejected@example.test", status="pending",
                       chuc_vu="THANH_VIEN")
        pending.set_password("test-password")
        db.session.add(pending)
        db.session.commit()
        self.login(1)
        with patch("app.routes.admin.send_email", return_value=False) as mail:
            response = self.csrf_request("POST", f"/admin/approve/{pending.id}",
                                        data={"action": "reject", "csrf_token": self.csrf()},
                                        follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(db.session.get(User, pending.id).status, "rejected")
        self.assertEqual(mail.call_args.args[0], "rejected@example.test")
        self.assertIn("Email thông báo chưa gửi được".encode(), response.data)

        self.app.config.update(MAIL_ENABLED=True, MAIL_SERVER="smtp.example.test", MAIL_PORT=587,
                               MAIL_USE_TLS=True, MAIL_USERNAME="club@example.test",
                               MAIL_PASSWORD="test-password", MAIL_DEFAULT_SENDER="club@example.test")
        server = MagicMock()
        server.__enter__.return_value = server
        with patch("app.mail.smtplib.SMTP", return_value=server) as smtp:
            from app.mail import send_email
            self.assertTrue(send_email("member@example.test", "Thông báo", "Nội dung"))
        smtp.assert_called_once_with("smtp.example.test", 587, timeout=10)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("club@example.test", "test-password")
        message = server.send_message.call_args.args[0]
        self.assertEqual(message["To"], "member@example.test")
        self.assertEqual(message["From"], "club@example.test")

    def test_only_cn_and_pcn_can_approve_registrations(self):
        db.session.add(Ban(id=2, ten_ban="Nhan su"))
        leader = User(mssv="TB001", ho_ten="Leader", ngay_sinh=date(2003, 1, 1),
                      sdt="0123456789", email="leader@example.test", status="approved",
                      chuc_vu="TB")
        leader.set_password("test-password")
        vice = User(mssv="PCN001", ho_ten="Vice", ngay_sinh=date(2003, 1, 1),
                    sdt="0123456789", email="vice@example.test", status="approved",
                    chuc_vu="PCN")
        vice.set_password("test-password")
        outsider = User(mssv="NEW003", ho_ten="Outside", ngay_sinh=date(2004, 1, 1),
                        sdt="0123456789", email="outside@example.test", status="pending",
                        chuc_vu="THANH_VIEN")
        outsider.set_password("test-password")
        db.session.add_all([leader, vice, outsider])
        db.session.flush()
        db.session.add_all([UserBan(user_id=leader.id, ban_id=1, is_truong_ban=True),
                            UserBan(user_id=outsider.id, ban_id=2)])
        db.session.commit()
        # Trưởng ban: không vào được trang duyệt, không duyệt được
        self.login(leader.id)
        self.assertEqual(self.client.get("/admin/approve").status_code, 403)
        denied = self.csrf_request("POST", f"/admin/approve/{outsider.id}",
                                   data={"action": "accept", "csrf_token": self.csrf()})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(db.session.get(User, outsider.id).status, "pending")
        # Phó chủ nhiệm: duyệt được
        self.login(vice.id)
        page = self.client.get("/admin/approve")
        self.assertIn(b"Outside", page.data)
        with patch("app.routes.admin.send_email") as mail:
            response = self.csrf_request("POST", f"/admin/approve/{outsider.id}",
                                        data={"action": "accept", "csrf_token": self.csrf()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(db.session.get(User, outsider.id).status, "approved")
        mail.assert_called_once()

    def test_ai_proposal_requires_approval_then_creates_event_and_tasks(self):
        self.login(1)
        token = self.csrf()
        start = datetime.utcnow() + timedelta(days=3)
        created = self.csrf_request("POST", "/api/v1/assistant/proposals", json={
            "kind": "event", "brief": "Workshop an toàn thông tin",
            "start_at": start.isoformat(),
            "end_at": (start + timedelta(hours=2)).isoformat(), "ban_id": 1},
            headers={"X-CSRF-Token": token})
        self.assertEqual(created.status_code, 201, created.json)
        proposal_id = created.json["id"]
        self.assertEqual(created.json["status"], "pending")
        self.assertEqual(Event.query.count(), 0)
        self.login(3)
        self.assertEqual(self.csrf_request("POST", f"/api/v1/assistant/proposals/{proposal_id}/review",
                                          json={"decision": "approved"}, headers={"X-CSRF-Token": token}).status_code, 403)
        self.login(1)
        approved = self.csrf_request("POST", f"/api/v1/assistant/proposals/{proposal_id}/review",
                                    json={"decision": "approved"}, headers={"X-CSRF-Token": token})
        self.assertEqual(approved.status_code, 200, approved.json)
        event_id = approved.json["result_ids"][0]
        self.assertEqual(Event.query.count(), 1)
        self.assertEqual(self.csrf_request("POST", f"/api/v1/assistant/proposals/{proposal_id}/review",
                                          json={"decision": "approved"}, headers={"X-CSRF-Token": token}).status_code, 400)
        task_proposal = self.csrf_request("POST", "/api/v1/assistant/proposals", json={
            "kind": "task", "brief": "Chuẩn bị tài liệu; Kiểm tra thiết bị",
            "event_id": event_id, "ban_id": 1,
            "deadline": (start + timedelta(hours=1)).isoformat()}, headers={"X-CSRF-Token": token})
        self.assertEqual(task_proposal.status_code, 201, task_proposal.json)
        self.assertEqual(Task.query.count(), 0)
        task_approved = self.csrf_request("POST",
            f"/api/v1/assistant/proposals/{task_proposal.json['id']}/review",
            json={"decision": "approved"}, headers={"X-CSRF-Token": token})
        self.assertEqual(task_approved.status_code, 200, task_approved.json)
        self.assertEqual(Task.query.count(), 2)

    def test_ai_proposal_web_preview_reject_and_model_output(self):
        self.app.config.update(AI_PROVIDER="anthropic", AI_API_KEY="test-shared-key")
        self.login(1)
        token = self.csrf()
        start = datetime.utcnow() + timedelta(days=4)
        model_json = json.dumps({"name": "Workshop Claude", "content": "Nội dung học tập",
                                 "type": "workshop", "location": "Phòng A", "capacity": 25})
        fake = BytesIO(json.dumps({"content": [{"type": "text", "text": model_json}]}).encode())
        with patch("app.services.assistant.urlopen", return_value=fake):
            created = self.csrf_request("POST", "/ai/proposals/new", data={
                "kind": "event", "brief": "Tổ chức workshop cho CLB",
                "start_at": start.strftime("%Y-%m-%dT%H:%M"),
                "end_at": (start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
                "csrf_token": token})
        self.assertEqual(created.status_code, 302)
        self.assertEqual(Event.query.count(), 0)
        proposal = AIProposal.query.one()
        self.assertEqual(proposal.payload["name"], "Workshop Claude")
        page = self.client.get("/ai")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Workshop Claude", page.data)
        rejected = self.csrf_request("POST", f"/ai/proposals/{proposal.id}/rejected", data={"csrf_token": token})
        self.assertEqual(rejected.status_code, 302)
        self.assertEqual(Event.query.count(), 0)
        self.assertEqual(db.session.get(AIProposal, proposal.id).status, "rejected")

    def test_role_transfer_uses_form_target(self):
        self.login(1)
        role_page = self.client.get("/admin/roles")
        self.assertEqual(role_page.status_code, 200)
        self.assertNotIn(b"onclick=", role_page.data)
        response = self.csrf_request("POST", "/admin/roles/nhuong-quyen", data={"target_id": "3", "csrf_token": self.csrf()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(response.location).status_code, 200)
        self.assertEqual(db.session.get(User, 1).chuc_vu, "THANH_VIEN")
        self.assertEqual(db.session.get(User, 3).chuc_vu, "CN")

    def test_treasurer_can_transfer_own_role(self):
        self.login(2)
        self.assertEqual(self.client.get("/admin/roles").status_code, 200)
        response = self.csrf_request("POST", "/admin/roles/nhuong-quyen", data={"target_id": "4", "csrf_token": self.csrf()})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(response.location).status_code, 200)
        self.assertEqual(db.session.get(User, 2).chuc_vu, "THANH_VIEN")
        self.assertEqual(db.session.get(User, 4).chuc_vu, "THUKY_THUQUY")

    def test_local_time_and_sensitive_form_csrf(self):
        self.assertEqual(parse_datetime("2026-09-28T16:00:00Z", "Giờ"),
                         datetime(2026, 9, 28, 23, 0))
        self.assertEqual(parse_datetime("2026-09-28T23:00:00", "Giờ"),
                         datetime(2026, 9, 28, 23, 0))
        self.login(1)
        self.assertEqual(self.client.post("/admin/roles/nhuong-quyen", data={"target_id": "3"}).status_code, 400)
        self.assertEqual(self.client.post("/api/v1/assistant/proposals", json={}).status_code, 400)
        self.assertEqual(db.session.get(User, 1).chuc_vu, "CN")

    def test_fund_page_uses_collection_payment_flow(self):
        self.login(2)
        token = self.csrf()
        today = club_now().date()
        response = self.csrf_request("POST", "/funds/collections/new", data={
            "csrf_token": token, "name": "Quỹ tháng", "type": "semester", "amount": "100.00",
            "start_date": today.isoformat(), "due_date": (today + timedelta(days=10)).isoformat(),
        })
        self.assertEqual(response.status_code, 302)
        fund = FundCollection.query.one()
        self.login(3)
        page = self.client.get("/funds")
        self.assertIn("Quỹ tháng".encode(), page.data)
        self.assertEqual(self.client.post(f"/funds/collections/{fund.id}/payments", data={
            "amount": "100.00", "method": "transfer"}).status_code, 400)
        # Thành viên gửi xác nhận KHÔNG nhập số tiền -> hệ thống lấy toàn bộ số còn phải đóng
        paid = self.csrf_request("POST", f"/funds/collections/{fund.id}/payments", data={
            "csrf_token": token, "method": "transfer", "transaction_code": "TX123"})
        self.assertEqual(paid.status_code, 302)
        payment = FundPayment.query.one()
        self.assertEqual(payment.trang_thai, "pending")
        self.assertEqual(str(payment.so_tien), "100.00")
        self.login(2)
        self.assertIn(b"TX123", self.client.get("/funds").data)
        reviewed = self.csrf_request("POST", f"/funds/payments/{payment.id}/review", data={
            "csrf_token": token, "decision": "confirmed"})
        self.assertEqual(reviewed.status_code, 302)
        self.assertEqual(db.session.get(FundPayment, payment.id).trang_thai, "confirmed")
        self.login(3)
        self.assertIn(b"paid", self.client.get("/funds").data)

    def test_event_page_registration_qr_and_feedback(self):
        self.login(1)
        token = self.csrf()
        start = club_now() + timedelta(days=1)
        created = self.csrf_request("POST", "/event/new", data={
            "csrf_token": token, "ten_su_kien": "Ngày hội", "ma_su_kien": "NH01",
            "thoi_gian_bat_dau": start.strftime("%Y-%m-%dT%H:%M"),
            "thoi_gian_ket_thuc": (start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
            "dia_diem": "Phòng A"})
        self.assertEqual(created.status_code, 302)
        event = Event.query.one()
        self.login(3)
        self.assertIn("Đăng ký tham gia".encode(), self.client.get(f"/event/{event.id}").data)
        registered = self.csrf_request("POST", f"/event/{event.id}/register", data={"csrf_token": token})
        self.assertEqual(registered.status_code, 302)
        self.assertEqual(EventRegistration.query.one().trang_thai, "registered")
        event.thoi_gian_bat_dau = club_now() - timedelta(minutes=5)
        event.thoi_gian_ket_thuc = club_now() + timedelta(hours=1)
        db.session.commit()
        self.login(1)
        qr = self.client.get(f"/api/v1/events/{event.id}/qr")
        self.assertEqual(qr.status_code, 200)
        self.login(3)
        path = f"/event/{event.id}/checkin/{qr.json['token']}"
        self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.csrf_request("POST", path, data={"csrf_token": token}).status_code, 302)
        self.assertEqual(Attendance.query.one().trang_thai, "late")
        self.login(1)
        self.assertEqual(self.csrf_request("POST", f"/event/{event.id}/ket-thuc").status_code, 302)
        self.login(3)
        self.assertEqual(self.csrf_request("POST", f"/event/{event.id}/feedback", data={
            "csrf_token": token, "rating": "5", "comment": "Tốt"}).status_code, 302)
        self.assertIn("Tốt".encode(), self.client.get(f"/event/{event.id}").data)

    def test_event_form_reports_specific_error_and_preserves_input(self):
        self.login(1)
        token = self.csrf()
        start = (club_now() + timedelta(days=1)).replace(second=0, microsecond=0)
        base = {
            "csrf_token": token, "ten_su_kien": "Test1", "ma_su_kien": "T1",
            "thoi_gian_bat_dau": start.strftime("%Y-%m-%dT%H:%M"),
            "thoi_gian_ket_thuc": (start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
            "dia_diem": "FPT University", "noi_dung": "Test1",
        }
        cases = [
            ({"thoi_gian_bat_dau": (start - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")},
             "Thời gian bắt đầu phải sau thời điểm hiện tại"),
            ({"thoi_gian_ket_thuc": (start - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")},
             "Thời gian kết thúc phải sau thời gian bắt đầu"),
            ({"han_dang_ky": (start - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")},
             "Hạn đăng ký phải nằm trong khoảng thời gian bắt đầu"),
            ({"han_dang_ky": (start + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")},
             "Hạn đăng ký phải nằm trong khoảng thời gian bắt đầu"),
        ]
        for change, message in cases:
            with self.subTest(message=message):
                form = {**base, **change}
                response = self.csrf_request("POST", "/event/new", data=form)
                self.assertEqual(response.status_code, 200)
                self.assertIn(message.encode(), response.data)
                for field in ("thoi_gian_bat_dau", "thoi_gian_ket_thuc"):
                    self.assertIn(f'value="{form[field]}"'.encode(), response.data)
                self.assertIn(b"FPT University", response.data)
        self.assertEqual(Event.query.count(), 0)

    def test_legacy_money_migration_keeps_rows_and_backup(self):
        path = Path(self.directory.name) / "legacy-money.db"
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE fund_period (id INTEGER PRIMARY KEY)")
            connection.execute('CREATE TABLE "user" (id INTEGER PRIMARY KEY)')
            connection.execute("INSERT INTO fund_period VALUES (1)")
            connection.execute('INSERT INTO "user" VALUES (1)')
            connection.execute("CREATE TABLE fund_transaction (id INTEGER PRIMARY KEY, period_id INTEGER NOT NULL REFERENCES fund_period(id), danh_muc VARCHAR(100) NOT NULL, noi_dung VARCHAR(255), ngay DATE NOT NULL, so_tien FLOAT NOT NULL, tao_boi_id INTEGER REFERENCES user(id))")
            connection.execute("INSERT INTO fund_transaction VALUES (1, 1, 'thu', '', '2026-09-28', 12.50, 1)")
        backup = migrate_legacy_money(path)
        self.assertTrue(backup.is_file())
        with sqlite3.connect(path) as connection:
            column = next(row for row in connection.execute("PRAGMA table_info(fund_transaction)") if row[1] == "so_tien")
            self.assertEqual(column[2], "NUMERIC(14, 2)")
            self.assertEqual(connection.execute("SELECT id, so_tien FROM fund_transaction").fetchone(), (1, 12.5))
        self.assertIsNone(migrate_legacy_money(path))

    def test_email_recipient_migration_preserves_sent_drafts(self):
        path = Path(self.directory.name) / "legacy-email.db"
        with sqlite3.connect(path) as connection:
            connection.execute('CREATE TABLE "user" (id INTEGER PRIMARY KEY)')
            connection.execute('INSERT INTO "user" VALUES (1)')
            connection.execute('INSERT INTO "user" VALUES (2)')
            connection.execute("""
                CREATE TABLE email_proposal (
                    id INTEGER PRIMARY KEY,
                    recipient_id INTEGER NOT NULL REFERENCES user(id),
                    recipient_email VARCHAR(120) NOT NULL,
                    request_text TEXT NOT NULL,
                    subject VARCHAR(180) NOT NULL,
                    body TEXT NOT NULL,
                    status VARCHAR(12) NOT NULL,
                    created_by_id INTEGER NOT NULL REFERENCES user(id),
                    reviewed_by_id INTEGER REFERENCES user(id),
                    created_at DATETIME NOT NULL,
                    reviewed_at DATETIME,
                    sent_at DATETIME
                )
            """)
            connection.execute("""
                INSERT INTO email_proposal
                    (id, recipient_id, recipient_email, request_text, subject, body,
                     status, created_by_id, reviewed_by_id, created_at, reviewed_at, sent_at)
                VALUES (1, 2, 'member@club.org', 'hello', 'Hello', 'Body',
                        'sent', 1, 1, '2026-09-29', '2026-09-29', '2026-09-29')
            """)
        backup = migrate_email_recipients(path)
        self.assertTrue(backup.is_file())
        with sqlite3.connect(path) as connection:
            column = next(row for row in connection.execute("PRAGMA table_info(email_proposal)")
                          if row[1] == "recipient_id")
            self.assertEqual(column[3], 0)
            self.assertEqual(connection.execute(
                "SELECT recipient_id, recipient_email, status FROM email_proposal WHERE id=1"
            ).fetchone(), (2, "member@club.org", "sent"))
            connection.execute("""
                INSERT INTO email_proposal
                    (id, recipient_id, recipient_email, request_text, subject, body,
                     status, created_by_id, created_at)
                VALUES (2, NULL, 'external@partner.org', 'book room', 'Hello', 'Body',
                        'pending', 1, '2026-09-29')
            """)
        self.assertIsNone(migrate_email_recipients(path))

    def test_natural_event_request_asks_for_missing_times_then_needs_approval(self):
        self.app.config.update(AI_PROVIDER="rules")
        self.login(1)
        token = self.csrf()
        question = "Tạo sự kiện Workshop bảo mật"
        self.assertEqual(self.client.post("/api/v1/assistant", json={
            "question": question}).status_code, 400)
        incomplete = self.csrf_request("POST", "/api/v1/assistant", json={"question": question},
                                      headers={"X-CSRF-Token": token})
        self.assertEqual(incomplete.status_code, 200, incomplete.json)
        self.assertEqual(incomplete.json["intent"], "proposal_needs_details")
        self.assertEqual(Event.query.count(), 0)
        self.assertIsNotNone(db.session.get(ConversationPending, incomplete.json["conversation_id"]))
        start = (club_now() + timedelta(days=3)).replace(second=0, microsecond=0)
        answer = f"Bắt đầu {start:%d/%m/%Y %H:%M} và kết thúc {start + timedelta(hours=2):%d/%m/%Y %H:%M}"
        created = self.csrf_request("POST", "/api/v1/assistant", json={"question": answer},
                                   headers={"X-CSRF-Token": token})
        self.assertEqual(created.status_code, 200, created.json)
        self.assertEqual(created.json["intent"], "ai_proposal")
        self.assertEqual(Event.query.count(), 0)
        self.assertIsNone(db.session.get(ConversationPending, created.json["conversation_id"]))
        proposal = AIProposal.query.one()
        self.assertEqual(proposal.kind, "event")
        self.assertIn("Workshop bảo mật", proposal.payload["name"])
        self.csrf_request("POST", f"/ai/proposals/{proposal.id}/approved", data={"csrf_token": token})
        self.assertEqual(Event.query.count(), 1)

    def test_natural_event_does_not_broaden_unknown_ban_and_reuses_pending_proposal(self):
        self.app.config.update(AI_PROVIDER="rules")
        self.login(1)
        token = self.csrf()
        start = (club_now() + timedelta(days=3)).replace(second=0, microsecond=0)
        question = (f"Tạo sự kiện Demo ban #999 từ {start:%d/%m/%Y %H:%M} "
                    f"đến {start + timedelta(hours=2):%d/%m/%Y %H:%M}")
        unknown = self.csrf_request("POST", "/api/v1/assistant", json={"question": question},
                                   headers={"X-CSRF-Token": token})
        self.assertEqual(unknown.json["intent"], "proposal_needs_details")
        self.assertIn("Không có ban #999", unknown.json["answer"])
        self.assertEqual(AIProposal.query.count(), 0)
        valid = question.replace("#999", "#1")
        first = self.csrf_request("POST", "/api/v1/assistant", json={"question": valid},
                                 headers={"X-CSRF-Token": token})
        again = self.csrf_request("POST", "/api/v1/assistant", json={"question": valid},
                                 headers={"X-CSRF-Token": token})
        self.assertEqual(first.json["intent"], "ai_proposal")
        self.assertIsNone(db.session.get(ConversationPending, first.json["conversation_id"]))
        self.assertEqual(again.json["proposal_id"], first.json["proposal_id"])
        self.assertEqual(AIProposal.query.count(), 1)

    def test_natural_task_request_names_assignee_and_refuses_three_bans_guess(self):
        self.app.config.update(AI_PROVIDER="rules")
        event_id = self.create_event()
        event = db.session.get(Event, event_id)
        self.login(1)
        token = self.csrf()
        deadline = event.thoi_gian_bat_dau + timedelta(hours=1)
        question = ("Giao task thiết kế poster cho Member 3 trong sự kiện "
                    f"Workshop ban Ky thuat hạn {deadline:%d/%m/%Y %H:%M}")
        created = self.csrf_request("POST", "/api/v1/assistant", json={"question": question},
                                   headers={"X-CSRF-Token": token})
        self.assertEqual(created.status_code, 200, created.json)
        self.assertEqual(created.json["intent"], "ai_proposal")
        proposal = AIProposal.query.filter_by(kind="task").one()
        self.assertEqual(proposal.payload["tasks"][0]["assignee_id"], 3)
        self.assertEqual(Task.query.count(), 0)
        self.csrf_request("POST", f"/ai/proposals/{proposal.id}/approved", data={"csrf_token": token})
        self.assertEqual(Task.query.one().assignee_id, 3)

        ambiguous = self.csrf_request("POST", "/api/v1/assistant", json={
            "question": "Chia task Tuyển NY cho Member 3 cho 3 ban"},
            headers={"X-CSRF-Token": token})
        self.assertEqual(ambiguous.status_code, 200, ambiguous.json)
        self.assertEqual(ambiguous.json["intent"], "proposal_needs_details")
        self.assertIn("Một task chỉ thuộc một ban", ambiguous.json["answer"])
        self.assertEqual(AIProposal.query.count(), 1)
        cancelled = self.csrf_request("POST", "/api/v1/assistant", json={"question": "hủy"},
                                     headers={"X-CSRF-Token": token})
        self.assertEqual(cancelled.json["intent"], "proposal_cancelled")
        self.assertIsNone(db.session.get(ConversationPending, cancelled.json["conversation_id"]))

    def test_natural_task_does_not_substitute_unknown_named_assignee(self):
        self.app.config.update(AI_PROVIDER="rules")
        event_id = self.create_event()
        event = db.session.get(Event, event_id)
        self.login(1)
        token = self.csrf()
        deadline = event.thoi_gian_bat_dau + timedelta(hours=1)
        question = ("Giao task thiết kế poster cho Người Không Tồn Tại trong sự kiện "
                    f"Workshop ban Ky thuat hạn {deadline:%d/%m/%Y %H:%M}")
        response = self.csrf_request("POST", "/api/v1/assistant", json={"question": question},
                                    headers={"X-CSRF-Token": token})
        self.assertEqual(response.json["intent"], "proposal_needs_details")
        self.assertIn("Không tìm thấy người", response.json["answer"])
        self.assertEqual(AIProposal.query.count(), 0)

    def test_approval_accepts_three_tasks_within_same_member_capacity(self):
        event_id = self.create_event()
        event = db.session.get(Event, event_id)
        self.login(1)
        token = self.csrf()
        proposal = self.csrf_request("POST", "/api/v1/assistant/proposals", json={
            "kind": "task", "brief": "Việc A; Việc B; Việc C",
            "event_id": event_id, "ban_id": 1, "assignee_id": 3,
            "deadline": (event.thoi_gian_bat_dau + timedelta(hours=1)).isoformat(),
        }, headers={"X-CSRF-Token": token})
        self.assertEqual(proposal.status_code, 201, proposal.json)
        reviewed = self.csrf_request("POST", f"/api/v1/assistant/proposals/{proposal.json['id']}/review",
                                    json={"decision": "approved"},
                                    headers={"X-CSRF-Token": token})
        self.assertEqual(reviewed.status_code, 200, reviewed.json)
        self.assertEqual(Task.query.filter_by(assignee_id=3).count(), 3)

    def test_explicit_assignee_is_not_lost_after_first_thirty_candidates(self):
        event_id = self.create_event()
        event = db.session.get(Event, event_id)
        for user_id in range(5, 35):
            member = User(id=user_id, mssv=f"SV{user_id}", ho_ten=f"Member {user_id}",
                          ngay_sinh=date(2003, 1, 1), sdt="0123456789",
                          email=f"member{user_id}@example.test", status="approved",
                          chuc_vu="THANH_VIEN")
            member.set_password("test-password")
            db.session.add(member)
            db.session.add(UserBan(user_id=user_id, ban_id=1))
        db.session.commit()
        self.login(1)
        token = self.csrf()
        proposal = self.csrf_request("POST", "/api/v1/assistant/proposals", json={
            "kind": "task", "brief": "Giao cho thành viên cuối", "event_id": event_id,
            "ban_id": 1, "assignee_id": 34,
            "deadline": (event.thoi_gian_bat_dau + timedelta(hours=1)).isoformat(),
        }, headers={"X-CSRF-Token": token})
        self.assertEqual(proposal.status_code, 201, proposal.json)
        self.assertEqual(AIProposal.query.one().payload["tasks"][0]["assignee_id"], 34)

    def test_digest_and_report_are_scoped_deterministic_and_source_backed(self):
        self.app.config.update(AI_PROVIDER="rules")
        now = club_now().replace(second=0, microsecond=0)
        event = Event(ten_su_kien="Họp tổng kết", ma_su_kien="TONGKET",
                      thoi_gian_bat_dau=now - timedelta(hours=2),
                      thoi_gian_ket_thuc=now - timedelta(hours=1),
                      trang_thai="da_ket_thuc", tao_boi_id=1)
        db.session.add(event)
        db.session.flush()
        task = Task(event_id=event.id, ten_task="Chuẩn bị tài liệu",
                    deadline=now - timedelta(minutes=30), assignee_id=3, ban_id=1,
                    trang_thai="hoan_thanh")
        db.session.add(task)
        reg = EventRegistration(event_id=event.id, user_id=3, trang_thai="registered")
        db.session.add(reg)
        db.session.flush()
        db.session.add(Attendance(registration_id=reg.id, trang_thai="on_time",
                                  checkin_luc=now - timedelta(hours=1)))
        fund = FundCollection(ten_khoan_thu="Quỹ mới", loai="event", so_tien=25,
                              ngay_bat_dau=now.date(), han_dong=now.date(),
                              tat_ca_thanh_vien=True, trang_thai="open", tao_boi_id=1)
        db.session.add(fund)
        db.session.flush()
        db.session.add(FundPayment(fund_id=fund.id, user_id=3, so_tien=25,
                                   phuong_thuc="cash", trang_thai="confirmed",
                                   xac_nhan_boi_id=1, xac_nhan_luc=datetime.utcnow()))
        db.session.commit()
        self.login(1)
        actor = db.session.get(User, 1)
        first = report_text(report_data(actor, "week"))
        second = report_text(report_data(actor, "week"))
        self.assertEqual(first, second)
        self.assertIn("1 sự kiện, 1 lượt có mặt", first)
        self.assertIn(f"event ID: {event.id}", first)
        self.assertIn("25 đ", first)
        digest = daily_digest(actor)
        self.assertEqual(digest, daily_digest(actor))
        self.assertIn("Nguồn: task, event", digest)
        answer = self.csrf_request("POST", "/api/v1/assistant", json={"question": "báo cáo tuần này"})
        self.assertEqual(answer.status_code, 200, answer.json)
        self.assertEqual(answer.json["provider"], "database")
        self.assertIn(f"event ID: {event.id}", answer.json["answer"])

    def test_report_snapshot_export_and_email_remain_reviewable(self):
        self.app.config.update(AI_PROVIDER="rules", MAIL_ENABLED=True)
        db.session.get(User, 1).email = "chair@club.org"
        now = club_now()
        event = Event(ten_su_kien="Báo cáo cũ", ma_su_kien="REPORT1",
                      thoi_gian_bat_dau=now - timedelta(hours=2),
                      thoi_gian_ket_thuc=now - timedelta(hours=1),
                      trang_thai="da_ket_thuc", tao_boi_id=1)
        db.session.add(event)
        db.session.commit()
        self.login(1)
        token = self.csrf()
        self.assertEqual(self.client.post("/ai/reports/week/save").status_code, 400)
        self.assertEqual(self.csrf_request("POST", "/ai/reports/week/save",
                                          data={"csrf_token": token}).status_code, 302)
        snapshot = AIReportSnapshot.query.one()
        self.assertEqual(self.csrf_request("POST", "/ai/reports/week/save",
                                          data={"csrf_token": token}).status_code, 302)
        self.assertEqual(AIReportSnapshot.query.count(), 1)
        event.ten_su_kien = "Báo cáo mới"
        db.session.commit()
        exported = self.client.get(f"/ai/reports/{snapshot.id}.xlsx")
        self.assertEqual(exported.status_code, 200)
        self.assertTrue(exported.data.startswith(b"PK"))
        self.assertEqual(snapshot.payload["events"][0]["name"], "Báo cáo cũ")
        self.assertNotIn("Báo cáo mới", snapshot.payload["events"][0]["name"])
        self.assertEqual(self.client.post(f"/ai/reports/{snapshot.id}/email-draft").status_code, 400)
        with patch("app.services.email_proposals.send_email") as send:
            drafted = self.csrf_request("POST", f"/ai/reports/{snapshot.id}/email-draft",
                                       data={"csrf_token": token})
            send.assert_not_called()
        self.assertEqual(drafted.status_code, 302)
        draft = EmailProposal.query.one()
        self.assertEqual(draft.status, "pending")
        self.assertIn("Báo cáo tuần", draft.body)
        self.login(3)
        self.assertEqual(self.client.get(f"/ai/reports/{snapshot.id}.xlsx").status_code, 403)
        self.login(1)
        db.session.get(User, 1).chuc_vu = "TB"
        db.session.commit()
        self.assertEqual(self.client.get(f"/ai/reports/{snapshot.id}.xlsx").status_code, 200)
        self.assertEqual(self.csrf_request("POST", f"/ai/reports/{snapshot.id}/email-draft",
                                          data={"csrf_token": token}).status_code, 302)

    def test_truong_ban_report_includes_other_ban_sources(self):
        self.app.config.update(AI_PROVIDER="rules")
        db.session.add(Ban(id=2, ten_ban="Nhan su"))
        leader = User(id=5, mssv="TB005", ho_ten="Truong ban ky thuat",
                      ngay_sinh=date(2003, 1, 1), sdt="0123456789",
                      email="leader@example.test", status="approved", chuc_vu="TB")
        outsider = User(id=6, mssv="SV006", ho_ten="Nguoi ban khac",
                        ngay_sinh=date(2003, 1, 1), sdt="0123456789",
                        email="outsider@example.test", status="approved",
                        chuc_vu="THANH_VIEN")
        leader.set_password("test-password")
        outsider.set_password("test-password")
        db.session.add_all([leader, outsider])
        db.session.flush()
        db.session.add_all([UserBan(user_id=5, ban_id=1, is_truong_ban=True),
                            UserBan(user_id=6, ban_id=2)])
        now = club_now().replace(second=0, microsecond=0)
        first = Event(ten_su_kien="Kỹ thuật", ma_su_kien="KT01",
                      thoi_gian_bat_dau=now - timedelta(hours=2),
                      thoi_gian_ket_thuc=now - timedelta(hours=1),
                      trang_thai="da_ket_thuc", tao_boi_id=1)
        second = Event(ten_su_kien="Nhân sự", ma_su_kien="NS01",
                       thoi_gian_bat_dau=now - timedelta(hours=2),
                       thoi_gian_ket_thuc=now - timedelta(hours=1),
                       trang_thai="da_ket_thuc", tao_boi_id=1)
        db.session.add_all([first, second])
        db.session.flush()
        db.session.add_all([EventTargetBan(event_id=first.id, ban_id=1),
                            EventTargetBan(event_id=second.id, ban_id=2)])
        fund = FundCollection(ten_khoan_thu="Quỹ", loai="event", so_tien=25,
                              ngay_bat_dau=now.date(), han_dong=now.date(),
                              tat_ca_thanh_vien=True, trang_thai="open", tao_boi_id=1)
        db.session.add(fund)
        db.session.flush()
        db.session.add_all([
            FundPayment(fund_id=fund.id, user_id=3, so_tien=25, phuong_thuc="cash",
                        trang_thai="confirmed", xac_nhan_boi_id=1,
                        xac_nhan_luc=datetime.utcnow()),
            FundPayment(fund_id=fund.id, user_id=6, so_tien=25, phuong_thuc="cash",
                        trang_thai="confirmed", xac_nhan_boi_id=1,
                        xac_nhan_luc=datetime.utcnow()),
        ])
        db.session.commit()
        payload = report_data(leader, "week")
        self.assertEqual([row["id"] for row in payload["events"]], [first.id, second.id])
        self.assertEqual([row["member_id"] for row in payload["payments"]], [3, 6])
        self.assertEqual(payload["scope_ban_ids"], [])


if __name__ == "__main__":
    unittest.main()
