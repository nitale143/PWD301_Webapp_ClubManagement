"""Quỹ: khoản thu là nguồn duy nhất; quỹ kỳ là một khoản thu; xác nhận thanh toán tự vào sổ thu chi."""

import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from flask import g

from tests.support import csrf_for
from app import create_app, db
from app.models import (
    Ban, FundCollection, FundPayment, FundPeriod, FundTransaction, MemberRecord, User, UserBan,
)
from app.services.assistant import RulesProvider
from app.services.common import DomainError, club_now
from app.services.funds import (
    adjust_fund, balance, create_collection, ledger_totals, owing_by_member, period_fund,
    record_confirmed_payment, record_payment, review_payment,
)

TREASURER, MEMBER_A, MEMBER_B, MEMBER_C = 1, 2, 3, 4


class UnifiedFundTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True, "SECRET_KEY": "fund-unified-secret-key-0123456789abcd",
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{Path(self.directory.name) / 'funds.db'}",
            "AI_PROVIDER": "rules", "SCHEDULER_ENABLED": False})
        self.client = self.app.test_client()
        self.context = self.app.test_request_context()
        self.context.push()
        db.session.add(Ban(id=1, ten_ban="Kỹ thuật"))
        for user_id, role in ((TREASURER, "THUKY_THUQUY"), (MEMBER_A, "THANH_VIEN"),
                              (MEMBER_B, "THANH_VIEN"), (MEMBER_C, "THANH_VIEN")):
            user = User(id=user_id, mssv=f"SE18000{user_id}", ho_ten=f"Member {user_id}",
                        ngay_sinh=date(2004, 1, 1), sdt=f"+8490000000{user_id}",
                        email=f"m{user_id}@example.com", status="approved", chuc_vu=role)
            user.set_password("test-password")
            db.session.add(user)
            db.session.add(MemberRecord(user_id=user_id, ngay_gia_nhap=date(2025, 1, 1)))
            db.session.add(UserBan(user_id=user_id, ban_id=1))
        self.today = club_now().date()
        self.period = FundPeriod(ten_ky="Fall 2026", ngay_bat_dau=self.today - timedelta(days=10),
                                 ngay_ket_thuc=self.today + timedelta(days=80), is_current=True)
        db.session.add(self.period)
        db.session.commit()
        self.quy_ky = create_collection(self.user(TREASURER), {
            "name": "Quỹ Fall 2026", "type": "semester", "amount": "100000",
            "start_date": self.today.isoformat(), "due_date": (self.today + timedelta(days=20)).isoformat(),
            "status": "open"})

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

    def test_collection_belongs_to_period_and_period_has_one_fund(self):
        self.assertEqual(self.quy_ky.period_id, self.period.id)
        self.assertEqual(period_fund(self.period).id, self.quy_ky.id)
        with self.assertRaises(DomainError):
            create_collection(self.user(TREASURER), {
                "name": "Quỹ trùng", "type": "semester", "amount": "1000", "start_date": self.today.isoformat(),
                "due_date": self.today.isoformat(), "status": "open"})
        other = create_collection(self.user(TREASURER), {
            "name": "Áo CLB", "type": "other", "amount": "50000", "start_date": self.today.isoformat(),
            "due_date": self.today.isoformat(), "status": "open"})
        self.assertEqual(other.period_id, self.period.id)

    def test_confirmed_payment_posts_to_ledger_exactly_once(self):
        payment = record_payment(self.user(MEMBER_A), self.quy_ky, self.user(MEMBER_A), {"method": "transfer"})
        self.assertEqual(FundTransaction.query.count(), 0)        # chờ xác nhận: chưa vào sổ
        review_payment(self.user(TREASURER), payment, "confirmed")
        row = FundTransaction.query.filter_by(payment_id=payment.id).one()
        self.assertEqual((row.period_id, row.so_tien), (self.period.id, Decimal("100000.00")))
        self.assertIn("Quỹ Fall 2026", row.danh_muc)
        rejected = record_payment(self.user(MEMBER_B), self.quy_ky, self.user(MEMBER_B), {"method": "cash"})
        review_payment(self.user(TREASURER), rejected, "rejected")
        self.assertEqual(FundTransaction.query.count(), 1)        # từ chối: không vào sổ
        self.assertEqual(ledger_totals(self.period)[2], Decimal("100000.00"))

    def test_status_reminder_and_assistant_read_the_same_source(self):
        record_confirmed_payment(self.user(TREASURER), self.quy_ky, self.user(MEMBER_A))
        record_confirmed_payment(self.user(TREASURER), self.quy_ky, self.user(MEMBER_B), amount="40000")
        adjust_fund(self.user(TREASURER), self.quy_ky, self.user(TREASURER), {"type": "exempt", "reason": "Thủ quỹ"})
        record_payment(self.user(MEMBER_C), self.quy_ky, self.user(MEMBER_C), {"method": "transfer"})
        states = {user_id: balance(self.quy_ky, self.user(user_id))["status"]
                  for user_id in (TREASURER, MEMBER_A, MEMBER_B, MEMBER_C)}
        self.assertEqual(states, {TREASURER: "exempt", MEMBER_A: "paid", MEMBER_B: "partial", MEMBER_C: "pending"})
        # Nhắc đóng: chỉ người còn thiếu; người đang chờ xác nhận và người được miễn không bị nhắc
        self.assertEqual([member.id for member in owing_by_member([self.quy_ky])], [MEMBER_B])
        self.login(TREASURER)
        with patch("app.routes.funds.send_email", return_value=True) as mail:
            self.client.post("/funds/nhac-dong-quy", data={"csrf_token": csrf_for(self.client)})
        self.assertEqual([call.args[0] for call in mail.call_args_list], ["m3@example.com"])
        self.assertIn("còn 60.000 đ", mail.call_args.args[2])
        # Trợ lý AI dùng cùng số liệu
        unpaid = RulesProvider().answer(self.user(TREASURER), "Ai chưa đóng quỹ?")["answer"]
        self.assertIn("SE180003", unpaid)
        self.assertNotIn("SE180002", unpaid)
        overview = RulesProvider().answer(self.user(TREASURER), "Kiểm tra tình trạng quỹ")["answer"]
        self.assertIn("đã đóng 2/4", overview)
        partial = RulesProvider().answer(self.user(TREASURER), "Ai đóng thiếu?")["answer"]
        self.assertIn("SE180003", partial)

    def test_pages_show_collection_status(self):
        record_confirmed_payment(self.user(TREASURER), self.quy_ky, self.user(MEMBER_A))
        self.login(TREASURER)
        page = self.client.get("/funds?tab=dues").get_data(as_text=True)
        self.assertIn("Ghi nhận đã đóng", page)
        self.assertIn("Quỹ Fall 2026", page)
        home = self.client.get("/home").get_data(as_text=True)
        self.assertIn("100.000", home)                  # số dư sổ thu chi gồm tiền thu qua khoản thu
        self.login(MEMBER_A)
        own = self.client.get("/funds?tab=dues").get_data(as_text=True)
        self.assertIn("Member 2", own)
        self.assertNotIn("Member 3", own)
        self.assertNotIn("Ghi nhận đã đóng", own)
        self.assertIn("100.000 / 100.000", self.client.get("/profile").get_data(as_text=True))

    def test_open_period_with_and_without_fund_amount(self):
        self.login(TREASURER)
        token = csrf_for(self.client)
        self.client.post("/funds/period/new", data={"csrf_token": token, "ten_ky": "Spring 2027",
                                                    "ngay_bat_dau": (self.today + timedelta(days=90)).isoformat()})
        spring = FundPeriod.query.filter_by(ten_ky="Spring 2027").one()
        self.assertIsNone(period_fund(spring))
        self.client.post("/funds/period/new", data={"csrf_token": token, "ten_ky": "Summer 2027",
                                                    "ngay_bat_dau": (self.today + timedelta(days=200)).isoformat(),
                                                    "so_tien_quy": "80000"})
        summer = FundPeriod.query.filter_by(ten_ky="Summer 2027").one()
        fund = period_fund(summer)
        self.assertEqual((fund.ten_khoan_thu, int(fund.so_tien)), ("Quỹ Summer 2027", 80000))
        self.assertEqual(fund.han_dong, summer.ngay_bat_dau + timedelta(days=30))

    def test_new_member_is_included_without_extra_rows(self):
        newcomer = User(id=9, mssv="SE180009", ho_ten="Newcomer", ngay_sinh=date(2004, 1, 1),
                        sdt="+84900000009", email="m9@example.com", status="approved", chuc_vu="THANH_VIEN")
        newcomer.set_password("test-password")
        db.session.add(newcomer)
        db.session.commit()
        self.assertEqual(balance(self.quy_ky, newcomer)["status"], "unpaid")
        self.assertEqual(FundPayment.query.count(), 0)
        self.assertEqual(FundCollection.query.count(), 1)


if __name__ == "__main__":
    unittest.main()
