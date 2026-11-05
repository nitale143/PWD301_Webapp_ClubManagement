"""Quỹ CLB – khoản thu là nguồn DUY NHẤT cho "phải đóng / đã đóng".

- Mỗi khoản thu (FundCollection) thuộc một kỳ (`period_id`). Quỹ kỳ = khoản thu loại `semester`,
  mỗi kỳ tối đa một, có thể tạo ngay khi mở kỳ.
- Tình trạng đóng của một thành viên = `balance(fund, member)`: phải đóng (sau miễn/giảm), đã đóng
  (thanh toán đã xác nhận), còn thiếu, trạng thái exempt/paid/partial/pending/overdue/unpaid.
- Thanh toán được xác nhận tự ghi một dòng "thu" vào sổ thu chi của kỳ (FundTransaction.payment_id),
  nên số dư sổ thu chi luôn gồm tiền thu qua khoản thu. Khoản chi vẫn ghi tay.
- Mọi nơi (trang Quỹ, hồ sơ, Excel, mail nhắc, trợ lý AI, báo cáo) đọc qua các hàm ở đây.
"""
from decimal import Decimal

from app import db
from app.models import (
    Ban, FundAdjustment, FundCollection, FundPayment, FundPeriod, FundTargetBan, FundTransaction,
    MemberRecord, User, UserBan,
)
from .access import approved, is_treasurer, require
from .activities import current_period, period_of
from .common import DomainError, audit, club_now, parse_date, parse_money
from app.timeutil import utcnow


# Loại khoản thu được phép TẠO MỚI: quỹ theo kỳ / quỹ sự kiện / khác.
# (Dữ liệu cũ monthly/yearly/voluntary vẫn hiển thị được, chỉ không tạo mới.)
COLLECTION_TYPES = {"semester", "event", "other"}
COLLECTION_STATES = {"draft", "open", "closed", "cancelled"}
PAYMENT_METHODS = {"cash", "transfer", "other"}
PERIOD_FUND = "semester"                         # loại khoản thu dùng làm quỹ kỳ
OWING_STATES = frozenset({"unpaid", "partial", "overdue"})   # còn phải đóng (không tính chờ xác nhận)
VISIBLE_STATES = ("open", "closed")              # khoản thu thành viên nhìn thấy


def target_members(fund):
    """Query thành viên phải đóng khoản thu (tất cả hoặc theo ban áp dụng), bỏ người đã rời/lưu
    trữ.
    """
    query = User.query.filter(User.status == "approved", User.chuc_vu != "ADMIN").outerjoin(
        MemberRecord, User.id == MemberRecord.user_id
    ).filter(MemberRecord.luu_tru_luc.is_(None)).filter(
        db.or_(MemberRecord.user_id.is_(None), MemberRecord.tinh_trang != "left")
    )
    if not fund.tat_ca_thanh_vien:
        ban_ids = [row.ban_id for row in FundTargetBan.query.filter_by(fund_id=fund.id)]
        query = query.filter(User.id.in_(db.session.query(UserBan.user_id).filter(UserBan.ban_id.in_(ban_ids))))
    return query.distinct()


def applies_to_member(fund, member):
    """True nếu khoản thu áp dụng cho thành viên."""
    return target_members(fund).filter(User.id == member.id).first() is not None


def period_fund(period):
    """Quỹ kỳ của một kỳ (khoản thu loại semester chưa hủy) hoặc None."""
    if period is None:
        return None
    return (FundCollection.query.filter(FundCollection.period_id == period.id,
                                        FundCollection.loai == PERIOD_FUND,
                                        FundCollection.trang_thai != "cancelled")
            .order_by(FundCollection.id).first())


def period_collections(period, states=VISIBLE_STATES):
    """Các khoản thu thuộc kỳ (mặc định: đang mở hoặc đã đóng sổ)."""
    if period is None:
        return []
    return (FundCollection.query.filter(FundCollection.period_id == period.id,
                                        FundCollection.trang_thai.in_(states))
            .order_by(FundCollection.loai != PERIOD_FUND, FundCollection.id).all())


def member_balances(member, funds):
    """[(khoản thu, balance)] cho các khoản thu áp dụng với thành viên."""
    return [(fund, balance(fund, member)) for fund in funds if applies_to_member(fund, member)]


def fund_member_rows(fund, member_ids=None):
    """[(thành viên, balance)] của một khoản thu; `member_ids` giới hạn phạm vi được xem."""
    query = target_members(fund)
    if member_ids is not None:
        query = query.filter(User.id.in_(member_ids))
    return [(member, balance(fund, member)) for member in query.order_by(User.ho_ten, User.id)]


def owing_by_member(funds, member_ids=None):
    """{thành viên: [(khoản thu, balance)]} những người còn phải đóng (chưa đóng, đóng thiếu, quá hạn)."""
    result = {}
    for fund in funds:
        for member, item in fund_member_rows(fund, member_ids):
            if item["status"] in OWING_STATES:
                result.setdefault(member, []).append((fund, item))
    return result


def ledger_totals(period):
    """(tổng thu, tổng chi, số dư) sổ thu chi của kỳ."""
    if period is None:
        zero = Decimal("0.00")
        return zero, zero, zero
    rows = FundTransaction.query.filter_by(period_id=period.id).all()
    income = sum((row.so_tien for row in rows if row.so_tien > 0), Decimal("0.00"))
    expense = sum((row.so_tien for row in rows if row.so_tien < 0), Decimal("0.00"))
    return income, expense, income + expense


def create_collection(actor, data):
    """Tạo khoản thu mới sau khi kiểm tra tên, loại, số tiền, ngày, ban áp dụng (MANAGE_FUNDS).
    Kỳ của khoản thu: `period_id` nếu truyền, ngược lại kỳ chứa ngày bắt đầu (hoặc kỳ hiện tại).
    Mỗi kỳ chỉ có một quỹ kỳ (loại semester)."""
    require(is_treasurer(actor))
    name = str(data.get("name", "")).strip()
    kind = str(data.get("type", "")).strip()
    status = str(data.get("status", "draft")).strip()
    if not name or len(name) > 180:
        raise DomainError("Tên khoản thu không hợp lệ.")
    if kind not in COLLECTION_TYPES or status not in COLLECTION_STATES:
        raise DomainError("Loại hoặc trạng thái khoản thu không hợp lệ.")
    start = parse_date(data.get("start_date"), "Ngày bắt đầu")
    due = parse_date(data.get("due_date"), "Hạn đóng")
    if due < start:
        raise DomainError("Hạn đóng phải sau ngày bắt đầu.")
    period = db.session.get(FundPeriod, data["period_id"]) if data.get("period_id") else None
    if data.get("period_id") and period is None:
        raise DomainError("Kỳ của khoản thu không tồn tại.")
    period = period or period_of(start) or current_period()
    if kind == PERIOD_FUND and period is not None and period_fund(period) is not None:
        raise DomainError(f"Kỳ {period.ten_ky} đã có quỹ kỳ. Hãy dùng loại khoản thu khác.")
    applies_all = bool(data.get("applies_to_all", True))
    ban_ids = data.get("department_ids", [])
    if not applies_all:
        if not isinstance(ban_ids, list) or not ban_ids:
            raise DomainError("Vui lòng chọn ít nhất một ban áp dụng.")
        try:
            ban_ids = list({int(value) for value in ban_ids})
        except (TypeError, ValueError):
            raise DomainError("Mã ban không hợp lệ.") from None
        if Ban.query.filter(Ban.id.in_(ban_ids)).count() != len(ban_ids):
            raise DomainError("Có ban không tồn tại.")
    fund = FundCollection(
        ten_khoan_thu=name, loai=kind,
        so_tien=parse_money(data.get("amount"), allow_zero=kind == "voluntary"),
        ngay_bat_dau=start, han_dong=due, tat_ca_thanh_vien=applies_all,
        mo_ta=str(data.get("description", "")).strip(), trang_thai=status,
        tao_boi_id=actor.id, period_id=period.id if period else None,
    )
    db.session.add(fund)
    db.session.flush()
    for ban_id in ([] if applies_all else ban_ids):
        db.session.add(FundTargetBan(fund_id=fund.id, ban_id=ban_id))
    audit(actor, "fund_collection_create", fund, {"amount": str(fund.so_tien)})
    if data.get("commit", True):
        db.session.commit()
    return fund


def balance(fund, member, as_of=None):
    """Số phải đóng (sau miễn/giảm), đã đóng (đã xác nhận), còn thiếu và trạng thái
    (exempt/paid/partial/overdue/unpaid) của một thành viên.
    """
    if not applies_to_member(fund, member):
        raise DomainError("Thành viên không thuộc đối tượng đóng khoản thu này.", 404)
    adjustment = FundAdjustment.query.filter_by(fund_id=fund.id, user_id=member.id).first()
    required = fund.so_tien
    if adjustment:
        if adjustment.loai == "exempt":
            required = Decimal("0.00")
        else:
            required = max(Decimal("0.00"), required - adjustment.so_tien)
    confirmed = FundPayment.query.filter_by(fund_id=fund.id, user_id=member.id, trang_thai="confirmed").all()
    paid = sum((payment.so_tien for payment in confirmed), Decimal("0.00"))
    remaining = max(Decimal("0.00"), required - paid)
    pending = FundPayment.query.filter_by(fund_id=fund.id, user_id=member.id, trang_thai="pending").count() > 0
    today = as_of or club_now().date()
    if adjustment and adjustment.loai == "exempt":
        state = "exempt"
    elif remaining == 0:
        state = "paid"
    elif fund.trang_thai == "open" and fund.han_dong < today:
        state = "overdue"
    elif paid > 0:
        state = "partial"
    elif pending:
        state = "pending"
    else:
        state = "unpaid"
    return {"required": required, "paid": paid, "remaining": remaining, "status": state}


def record_payment(actor, fund, member, data):
    """Ghi nhận một lần đóng tiền (pending); chặn khi khoản thu không mở, sai phương thức, hoặc số
    tiền vượt phần còn thiếu (tính cả khoản đang chờ xác nhận).
    """
    require(approved(actor) and (actor.id == member.id or is_treasurer(actor)))
    if fund.trang_thai != "open":
        raise DomainError("Khoản thu hiện không mở nhận thanh toán.")
    if not applies_to_member(fund, member):
        raise DomainError("Thành viên không thuộc đối tượng đóng khoản thu này.")
    method = str(data.get("method", "")).strip()
    if method not in PAYMENT_METHODS:
        raise DomainError("Phương thức thanh toán không hợp lệ.")
    current = balance(fund, member)
    pending = FundPayment.query.filter_by(
        fund_id=fund.id, user_id=member.id, trang_thai="pending"
    ).all()
    pending_total = sum((row.so_tien for row in pending), Decimal("0.00"))
    due_now = current["remaining"] - pending_total
    raw_amount = str(data.get("amount") or "").strip()
    if raw_amount:
        # Thủ quỹ ghi hộ / API / khoản tự nguyện cũ: vẫn cho nhập số tiền cụ thể
        amount = parse_money(raw_amount)
    else:
        # Thành viên chỉ bấm "Gửi xác nhận": tự lấy toàn bộ số còn phải đóng
        if due_now <= 0:
            raise DomainError("Bạn đã gửi xác nhận đủ số tiền, vui lòng chờ thủ quỹ xác nhận.")
        amount = due_now
    if fund.loai != "voluntary" and amount > due_now:
        raise DomainError("Số tiền đóng vượt số tiền còn thiếu hoặc đang chờ xác nhận.")
    payment = FundPayment(
        fund_id=fund.id, user_id=member.id,
        so_tien=amount,
        ngay_dong=utcnow(), phuong_thuc=method,
        ma_giao_dich=str(data.get("transaction_code", "")).strip()[:100],
        ghi_chu=str(data.get("note", "")).strip(), trang_thai="pending",
    )
    db.session.add(payment)
    db.session.flush()
    audit(actor, "fund_payment_create", payment, {"amount": str(payment.so_tien), "fund_id": fund.id})
    db.session.commit()
    return payment


def review_payment(actor, payment, decision, note=""):
    """Xác nhận/từ chối thanh toán đang chờ bằng UPDATE có điều kiện (chống hai người duyệt cùng
    lúc).
    """
    require(is_treasurer(actor))
    if payment.trang_thai != "pending":
        raise DomainError("Chỉ có thể duyệt giao dịch đang chờ xác nhận.")
    if decision not in {"confirmed", "rejected"}:
        raise DomainError("Quyết định xác nhận không hợp lệ.")
    claimed = db.session.execute(
        db.update(FundPayment).where(FundPayment.id == payment.id, FundPayment.trang_thai == "pending")
        .values(trang_thai=decision, xac_nhan_boi_id=actor.id)
    )
    if claimed.rowcount != 1:
        raise DomainError("Giao dịch đã được xử lý.")
    payment.trang_thai = decision
    payment.xac_nhan_boi_id = actor.id
    payment.xac_nhan_luc = utcnow()
    payment.ghi_chu = str(note).strip() or payment.ghi_chu
    audit(actor, f"fund_payment_{decision}", payment, {"amount": str(payment.so_tien)})
    if decision == "confirmed":
        _post_to_ledger(actor, payment)
    db.session.commit()
    return payment


def _post_to_ledger(actor, payment):
    """Ghi một dòng thu vào sổ thu chi của kỳ cho thanh toán vừa xác nhận (một thanh toán một dòng)."""
    if FundTransaction.query.filter_by(payment_id=payment.id).first():
        return None
    fund = db.session.get(FundCollection, payment.fund_id)
    today = club_now().date()
    period = (db.session.get(FundPeriod, fund.period_id) if fund.period_id else None) \
        or period_of(today) or current_period()
    if period is None:
        return None
    member = db.session.get(User, payment.user_id)
    row = FundTransaction(
        period_id=period.id, danh_muc=f"Thu: {fund.ten_khoan_thu}"[:100],
        noi_dung=f"GD-{payment.id} · {member.ho_ten} ({member.mssv})"[:255],
        ngay=today, so_tien=payment.so_tien, tao_boi_id=actor.id, payment_id=payment.id,
    )
    db.session.add(row)
    return row


def record_confirmed_payment(actor, fund, member, method="cash", amount=None, note=""):
    """Thủ quỹ ghi nhận thành viên đã đóng (vd. tiền mặt) và xác nhận luôn; mặc định đóng đủ số còn thiếu."""
    require(is_treasurer(actor))
    payment = record_payment(actor, fund, member, {
        "method": method, "amount": amount or "", "note": note or "Thủ quỹ ghi nhận trực tiếp"})
    return review_payment(actor, payment, "confirmed")


def adjust_fund(actor, fund, member, data):
    """Miễn hoặc giảm khoản thu cho một thành viên, bắt buộc lý do."""
    require(is_treasurer(actor))
    if not applies_to_member(fund, member):
        raise DomainError("Thành viên không thuộc đối tượng của khoản thu.")
    reason = str(data.get("reason", "")).strip()
    kind = str(data.get("type", "")).strip()
    if not reason:
        raise DomainError("Miễn hoặc giảm tiền cần có lý do.")
    if kind not in {"exempt", "reduce"}:
        raise DomainError("Loại điều chỉnh không hợp lệ.")
    amount = parse_money(data.get("amount", 0), allow_zero=kind == "exempt")
    if kind == "reduce" and amount > fund.so_tien:
        raise DomainError("Số tiền giảm không được vượt khoản phải đóng.")
    adjustment = FundAdjustment.query.filter_by(fund_id=fund.id, user_id=member.id).first()
    if adjustment is None:
        adjustment = FundAdjustment(fund_id=fund.id, user_id=member.id)
        db.session.add(adjustment)
    adjustment.loai = kind
    adjustment.so_tien = Decimal("0.00") if kind == "exempt" else amount
    adjustment.ly_do = reason
    adjustment.tao_boi_id = actor.id
    adjustment.tao_luc = utcnow()
    db.session.flush()
    audit(actor, "fund_adjustment", adjustment, {"type": kind, "amount": str(adjustment.so_tien), "reason": reason})
    db.session.commit()
    return adjustment
