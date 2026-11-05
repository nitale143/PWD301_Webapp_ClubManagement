from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from app import db
from app.models import AuditLog, MemberRecord
from app.timeutil import club_now, to_club_time  # noqa: F401  (club_now: dùng chung qua services.common)


class DomainError(Exception):
    """Lỗi nghiệp vụ có thông báo tiếng Việt và HTTP status (mặc định 400); route bắt và hiển thị
    cho người dùng.
    """
    def __init__(self, message, status=400):
        """Lưu thông báo và status."""
        super().__init__(message)
        self.status = status


def parse_date(value, label):
    """Đọc ngày dạng YYYY-MM-DD (hoặc date/datetime); sai → DomainError."""
    try:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise DomainError(f"{label} không hợp lệ (YYYY-MM-DD).") from None


def parse_datetime(value, label):
    """Đọc thời gian ISO 8601; có múi giờ thì đổi về giờ CLB (naive)."""
    try:
        parsed = datetime.fromisoformat(str(value))
        return to_club_time(parsed)
    except (TypeError, ValueError):
        raise DomainError(f"{label} không hợp lệ (ISO 8601).") from None


def parse_money(value, label="Số tiền", allow_zero=False):
    """Đọc số tiền Decimal tối đa 2 chữ số thập phân, không âm; `allow_zero` cho phép 0."""
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number.as_tuple().exponent < -2:
            raise InvalidOperation
        number = number.quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise DomainError(f"{label} phải là số tiền hợp lệ, tối đa 2 chữ số thập phân.") from None
    if number < 0 or (number == 0 and not allow_zero):
        raise DomainError(f"{label} phải {'lớn hơn hoặc bằng' if allow_zero else 'lớn hơn'} 0.")
    if number >= Decimal("1000000000000"):
        raise DomainError(f"{label} vượt giới hạn cho phép.")
    return number


def audit(actor, action, obj, details=None):
    """Thêm một dòng AuditLog (chưa commit) cho thao tác của `actor` trên `obj`."""
    entry = AuditLog(
        actor_id=getattr(actor, "id", None), action=action,
        object_type=obj.__class__.__name__, object_id=str(getattr(obj, "id", None) or getattr(obj, "user_id", "")),
        details=details or {},
    )
    db.session.add(entry)
    return entry


def member_record(user):
    """Lấy MemberRecord của user, chưa có thì tạo mới (ngày gia nhập = ngày tạo tài khoản)."""
    record = db.session.get(MemberRecord, user.id)
    if record is None:
        record = MemberRecord(user_id=user.id, ngay_gia_nhap=(user.ngay_tao or club_now()).date())
        db.session.add(record)
    return record
