from decimal import Decimal

from app import db
from app.models import (
    Ban, FundAdjustment, FundCollection, FundPayment, FundTargetBan,
    MemberRecord, User, UserBan,
)
from .access import approved, is_treasurer, require
from .common import DomainError, audit, club_now, parse_date, parse_money
from app.timeutil import utcnow


# Loại khoản thu được phép TẠO MỚI: quỹ theo kỳ / quỹ sự kiện / khác.
# (Dữ liệu cũ monthly/yearly/voluntary vẫn hiển thị được, chỉ không tạo mới.)
COLLECTION_TYPES = {"semester", "event", "other"}
COLLECTION_STATES = {"draft", "open", "closed", "cancelled"}
PAYMENT_METHODS = {"cash", "transfer", "other"}


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


def create_collection(actor, data):
    """Tạo khoản thu mới sau khi kiểm tra tên, loại, số tiền, ngày, ban áp dụng (MANAGE_FUNDS)."""
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
        tao_boi_id=actor.id,
    )
    db.session.add(fund)
    db.session.flush()
    for ban_id in ([] if applies_all else ban_ids):
        db.session.add(FundTargetBan(fund_id=fund.id, ban_id=ban_id))
    audit(actor, "fund_collection_create", fund, {"amount": str(fund.so_tien)})
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
    db.session.commit()
    return payment


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
