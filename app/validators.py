"""Quy tắc kiểm tra dữ liệu dùng chung cho form (WTForms) và REST API.

Nguyên tắc "Zero Trust" (Ch4 – slide 28-29): mọi dữ liệu người dùng gửi lên
đều được kiểm tra lại ở server, kể cả khi trình duyệt đã kiểm tra.
"""
import re
import unicodedata

from wtforms.validators import ValidationError

# MSSV: <campus><ngành><6 số>. Campus H/D/Q/C/S, ngành A/S/E, vd SE201256.
MSSV_RE = re.compile(r"^[HDQCS][ASE]\d{6}$")
MSSV_EXAMPLE = "SE201256"
# Tên đăng nhập của tài khoản quản trị hệ thống (không phải MSSV).
ADMIN_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")
# SĐT lưu dạng +84 + 9 số; người dùng chỉ nhập 9 số sau +84.
PHONE_PREFIX = "+84"
PHONE_LOCAL_RE = re.compile(r"^[1-9]\d{8}$")
PHONE_RE = re.compile(r"^\+84[1-9]\d{8}$")
# Facebook chỉ nhận đúng một dạng: https://facebook.com/<userID>.
FACEBOOK_PREFIX = "https://facebook.com/"
FACEBOOK_ID_RE = re.compile(r"^[A-Za-z0-9.]{1,50}$")
FACEBOOK_RE = re.compile(r"^https://facebook\.com/[A-Za-z0-9.]{1,50}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

PASSWORD_MIN = 8
PASSWORD_MAX = 64
COMMON_PASSWORDS = frozenset({
    "12345678", "123456789", "1234567890", "password", "password1", "password123",
    "qwerty123", "11111111", "abc12345", "matkhau123", "iloveyou1", "admin123",
})


def contains_control(value):
    """True nếu chuỗi có ký tự điều khiển (xuống dòng, tab, ký tự ẩn…)."""
    return any(unicodedata.category(char).startswith("C") for char in value or "")


def normalize_mssv(value):
    """Bỏ khoảng trắng và viết hoa MSSV (se201256 → SE201256)."""
    return str(value or "").strip().upper()


def normalize_phone(value):
    """Chuẩn hóa SĐT về dạng +84xxxxxxxxx; trả None nếu không hợp lệ.

    Nhận 9 số sau +84 (dạng chuẩn trên form). Để thân thiện, cũng nhận khi
    người dùng dán cả "+84…", "84…" hoặc "0…" (10 số); khoảng trắng, dấu chấm,
    gạch ngang được bỏ qua.
    """
    digits = re.sub(r"[\s.\-()]", "", str(value or ""))
    if digits.startswith("+84"):
        digits = digits[3:]
    elif digits.startswith("84") and len(digits) == 11:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 10:
        digits = digits[1:]
    return PHONE_PREFIX + digits if PHONE_LOCAL_RE.fullmatch(digits) else None


def phone_local_part(value):
    """Phần 9 số sau +84 để hiển thị lại trong ô nhập."""
    value = str(value or "")
    return value[len(PHONE_PREFIX):] if value.startswith(PHONE_PREFIX) else value


def normalize_facebook(value):
    """Chuẩn hóa link Facebook về https://facebook.com/<userID>; trả None nếu sai dạng.

    Chỉ nhận userID (chữ, số, dấu chấm) hoặc đúng link https://facebook.com/<userID>.
    Không nhận http://, www., m., fb.com, tham số ?id=… hay đường dẫn con.
    """
    raw = str(value or "").strip()
    user_id = raw[len(FACEBOOK_PREFIX):] if raw.startswith(FACEBOOK_PREFIX) else raw
    return FACEBOOK_PREFIX + user_id if FACEBOOK_ID_RE.fullmatch(user_id) else None


def facebook_id_part(value):
    """Phần userID sau https://facebook.com/ để hiển thị lại trong ô nhập."""
    value = str(value or "")
    return value[len(FACEBOOK_PREFIX):] if value.startswith(FACEBOOK_PREFIX) else value


def password_problems(password, mssv=None):
    """Trả về danh sách lý do mật khẩu chưa đủ mạnh (rỗng = hợp lệ).

    Chính sách (Ch6 – slide 79-80): 8–64 ký tự, có ít nhất một chữ cái và một
    chữ số, không chứa MSSV, không nằm trong danh sách mật khẩu phổ biến.
    """
    problems = []
    if not isinstance(password, str) or not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        return [f"Mật khẩu phải có từ {PASSWORD_MIN} đến {PASSWORD_MAX} ký tự."]
    if not re.search(r"[^\W\d_]", password):
        problems.append("Mật khẩu phải có ít nhất một chữ cái.")
    if not re.search(r"\d", password):
        problems.append("Mật khẩu phải có ít nhất một chữ số.")
    if mssv and len(mssv) >= 4 and mssv.casefold() in password.casefold():
        problems.append("Mật khẩu không được chứa MSSV.")
    if password.casefold() in COMMON_PASSWORDS:
        problems.append("Mật khẩu quá phổ biến, hãy chọn mật khẩu khác.")
    return problems


class StrongPassword:
    """Validator WTForms tùy biến dùng `password_problems` (Ch6 – slide 80, Ch4 – slide 35).

    `mssv_field`: tên field MSSV trong cùng form (nếu có) để chặn mật khẩu chứa MSSV.
    """

    def __init__(self, mssv_field=None):
        """`mssv_field`: tên field MSSV trong form để chặn mật khẩu chứa MSSV."""
        self.mssv_field = mssv_field

    def __call__(self, form, field):
        """Chạy password_problems; có vấn đề thì báo ValidationError."""
        mssv = None
        if self.mssv_field and self.mssv_field in form:
            mssv = (form[self.mssv_field].data or "").strip()
        elif getattr(form, "mssv_value", None):
            mssv = form.mssv_value
        problems = password_problems(field.data or "", mssv)
        if problems:
            raise ValidationError(" ".join(problems))


class NoControlChars:
    """Chặn ký tự điều khiển trong các ô văn bản một dòng."""

    def __init__(self, message="Dữ liệu chứa ký tự không hợp lệ."):
        """Thông báo lỗi tùy chỉnh."""
        self.message = message

    def __call__(self, form, field):
        """Báo lỗi nếu chuỗi có ký tự điều khiển."""
        if isinstance(field.data, str) and contains_control(field.data):
            raise ValidationError(self.message)
