"""Các form của webapp viết bằng Flask-WTF / WTForms (Ch4 – Web forms).

Mỗi form là một class kế thừa `BaseForm` (FlaskForm):
- CSRF token được Flask-WTF thêm tự động (render bằng `form.hidden_tag()`).
- Validator chạy ở server khi gọi `form.validate_on_submit()`.
- Validator tùy biến viết dạng `validate_<tên_field>` (inline) hoặc class
  (xem app/validators.py).

Thông báo lỗi mặc định của WTForms là tiếng Anh, nên `BaseForm.Meta` cung cấp
bản dịch tiếng Việt cho các câu thường gặp.
"""
from datetime import date

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import (
    BooleanField, DateField, DateTimeLocalField, HiddenField, PasswordField, RadioField,
    StringField, SubmitField, TextAreaField,
)
from wtforms.validators import (
    DataRequired, Email, EqualTo, InputRequired, Length, Optional, Regexp,
    ValidationError,
)

from app.validators import (
    FACEBOOK_RE, MSSV_EXAMPLE, MSSV_RE, PHONE_RE, NoControlChars, StrongPassword,
    normalize_facebook, normalize_mssv, normalize_phone,
)

# Ô mật khẩu khi ĐĂNG NHẬP / xác nhận lại nhận tới 128 ký tự để tài khoản cũ (đặt trước khi
# giới hạn 64 ký tự) vẫn đăng nhập được; mật khẩu MỚI bị giới hạn 8–64 bởi StrongPassword.
LOGIN_PASSWORD_MAX = 128

# --------------------------------------------------------------------------
# Bản dịch thông báo lỗi WTForms sang tiếng Việt
# --------------------------------------------------------------------------
_VI_MESSAGES = {
    "This field is required.": "Vui lòng nhập trường này.",
    "Not a valid date value.": "Ngày không hợp lệ.",
    "Not a valid datetime value.": "Thời gian không hợp lệ.",
    "Invalid email address.": "Email không hợp lệ.",
    "Field must be between %(min)d and %(max)d characters long.": "Độ dài phải từ %(min)d đến %(max)d ký tự.",
    "Field cannot be longer than %(max)d characters.": "Không được dài quá %(max)d ký tự.",
    "Field cannot be longer than %(max)d character.": "Không được dài quá %(max)d ký tự.",
    "Field must be at least %(min)d characters long.": "Phải có ít nhất %(min)d ký tự.",
    "Field must be at least %(min)d character long.": "Phải có ít nhất %(min)d ký tự.",
    "Field must be equal to %(other_name)s.": "Giá trị không khớp.",
    "Not a valid integer value.": "Phải là số nguyên.",
    "Not a valid decimal value.": "Phải là số.",
    "Not a valid choice.": "Lựa chọn không hợp lệ.",
    "Invalid Choice: could not coerce.": "Lựa chọn không hợp lệ.",
    "Invalid choice(s): one or more data inputs could not be coerced.": "Lựa chọn không hợp lệ.",
    "'%(value)s' is not a valid choice for this field.": "Lựa chọn không hợp lệ.",
    "'%(value)s' are not valid choices for this field.": "Lựa chọn không hợp lệ.",
    "Number must be at least %(min)s.": "Số phải lớn hơn hoặc bằng %(min)s.",
    "Number must be at most %(max)s.": "Số phải nhỏ hơn hoặc bằng %(max)s.",
    "Number must be between %(min)s and %(max)s.": "Số phải trong khoảng %(min)s đến %(max)s.",
    "Invalid input.": "Dữ liệu không hợp lệ.",
    "Invalid URL.": "Đường dẫn không hợp lệ.",
    "The CSRF token is missing.": "Phiên biểu mẫu không hợp lệ. Hãy tải lại trang.",
    "The CSRF token is invalid.": "Phiên biểu mẫu không hợp lệ. Hãy tải lại trang.",
    "The CSRF token has expired.": "Phiên biểu mẫu đã hết hạn. Hãy tải lại trang.",
    "The CSRF tokens do not match.": "Phiên biểu mẫu không hợp lệ. Hãy tải lại trang.",
    "The CSRF session token is missing.": "Phiên biểu mẫu không hợp lệ. Hãy tải lại trang.",
}


class _VietnameseTranslations:
    """Bộ dịch tối giản WTForms: đổi thông báo lỗi tiếng Anh sang tiếng Việt theo bảng
    _VI_MESSAGES.
    """
    def gettext(self, string):
        """Dịch một thông báo; không có trong bảng thì giữ nguyên."""
        return _VI_MESSAGES.get(string, string)

    def ngettext(self, singular, plural, n):
        """Dịch thông báo có dạng số ít/số nhiều."""
        return _VI_MESSAGES.get(singular if n == 1 else plural, singular if n == 1 else plural)


class BaseForm(FlaskForm):
    """FlaskForm có thông báo lỗi tiếng Việt."""

    class Meta(FlaskForm.Meta):
        """Meta của WTForms: gắn bộ dịch tiếng Việt cho mọi form kế thừa BaseForm."""
        def get_translations(self, form):
            """Trả về bộ dịch tiếng Việt cho form."""
            return _VietnameseTranslations()


def _strip(value):
    """Filter WTForms: bỏ khoảng trắng đầu/cuối của chuỗi."""
    return value.strip() if isinstance(value, str) else value


def _lower_strip(value):
    """Filter WTForms: bỏ khoảng trắng và đổi về chữ thường (dùng cho email)."""
    return value.strip().lower() if isinstance(value, str) else value


def _phone_filter(value):
    """Filter: chuẩn hóa SĐT về +84xxxxxxxxx; sai định dạng thì giữ nguyên để Regexp báo lỗi."""
    return (normalize_phone(value) or value.strip()) if isinstance(value, str) else value


def _facebook_filter(value):
    """Filter: chuẩn hóa userID / link về https://facebook.com/<userID>; sai thì giữ nguyên."""
    return (normalize_facebook(value) or value.strip()) if isinstance(value, str) else value


def _mssv_filter(value):
    """Filter: bỏ khoảng trắng và viết hoa MSSV."""
    return normalize_mssv(value) if isinstance(value, str) else value


# --------------------------------------------------------------------------
# Xác thực (Ch6)
# --------------------------------------------------------------------------
class LoginForm(BaseForm):
    """Form đăng nhập: MSSV, mật khẩu, ghi nhớ đăng nhập, `next` (trang quay lại sau khi đăng
    nhập).
    """
    mssv = StringField("MSSV / tên đăng nhập", filters=[_strip], validators=[DataRequired(), Length(max=20)])
    password = PasswordField("Mật khẩu", validators=[DataRequired(), Length(max=LOGIN_PASSWORD_MAX)])
    remember = BooleanField("Ghi nhớ đăng nhập trên thiết bị này")
    next = HiddenField()
    submit = SubmitField("Đăng nhập")


class RegistrationForm(BaseForm):
    """Đơn đăng ký thành viên. Danh sách ban (`ban_hoat_dong.choices`) được gán trong view;
    mỗi người chỉ chọn MỘT ban. SĐT lưu dạng +84xxxxxxxxx, Facebook dạng https://facebook.com/userID.
    """

    ho_ten = StringField("Họ và tên", filters=[_strip],
                         validators=[DataRequired(), Length(min=2, max=100), NoControlChars()])
    mssv = StringField("MSSV", filters=[_mssv_filter], validators=[
        DataRequired(), Regexp(MSSV_RE, message=f"MSSV không đúng định dạng (vd: {MSSV_EXAMPLE}).")])
    ngay_sinh = DateField("Ngày sinh", validators=[DataRequired(message="Ngày sinh không hợp lệ.")])
    sdt = StringField("Số điện thoại", filters=[_phone_filter], validators=[
        DataRequired(), Length(max=20),
        Regexp(PHONE_RE, message="Nhập 9 số sau +84 (vd: 912345678).")])
    email = StringField("Email", filters=[_lower_strip],
                        validators=[DataRequired(), Length(max=120), Email(message="Email không hợp lệ.")])
    facebook_link = StringField("Facebook", filters=[_facebook_filter], validators=[
        DataRequired(), Length(max=80), NoControlChars(),
        Regexp(FACEBOOK_RE, message="Chỉ nhận userID Facebook (chữ, số, dấu chấm), "
                                    "tức link dạng https://facebook.com/userID.")])
    ban_hoat_dong = RadioField("Ban hoạt động", choices=[], coerce=int,
                               validators=[InputRequired(message="Vui lòng chọn một ban hoạt động.")])
    password = PasswordField("Mật khẩu", validators=[DataRequired(), StrongPassword(mssv_field="mssv")])
    confirm_password = PasswordField("Nhập lại mật khẩu", validators=[
        DataRequired(), EqualTo("password", message="Mật khẩu nhập lại không khớp.")])
    submit = SubmitField("Gửi đăng ký")

    def validate_ngay_sinh(self, field):
        """Ngày sinh phải từ năm 1980 trở đi và trước hôm nay."""
        if field.data and (field.data.year < 1980 or field.data >= date.today()):
            raise ValidationError("Ngày sinh không hợp lệ.")


    # Validator kiểm tra trùng lặp trong database (Ch4 – slide 36-37)
    def validate_mssv(self, field):
        """Chặn MSSV đã có tài khoản (kiểm tra trùng trong database)."""
        from app.models import User
        if User.query.filter_by(mssv=field.data).first():
            raise ValidationError("MSSV này đã được đăng ký.")

    def validate_email(self, field):
        """Chặn email đã được dùng cho tài khoản khác."""
        from app.models import User
        if User.query.filter(User.email == field.data).first():
            raise ValidationError("Email này đã được dùng cho tài khoản khác.")


class ChangePasswordForm(BaseForm):
    """Form đổi mật khẩu: mật khẩu hiện tại, mật khẩu mới (StrongPassword) và nhập lại."""
    current_password = PasswordField("Mật khẩu hiện tại", validators=[DataRequired(), Length(max=LOGIN_PASSWORD_MAX)])
    new_password = PasswordField("Mật khẩu mới", validators=[DataRequired(), StrongPassword()])
    confirm_password = PasswordField("Nhập lại mật khẩu mới", validators=[
        DataRequired(), EqualTo("new_password", message="Mật khẩu nhập lại không khớp.")])
    submit = SubmitField("Đổi mật khẩu")


class ForgotPasswordForm(BaseForm):
    """Form quên mật khẩu: nhập email đã đăng ký để nhận link đặt lại."""
    email = StringField("Email đã đăng ký", filters=[_lower_strip],
                        validators=[DataRequired(), Length(max=120), Email(message="Email không hợp lệ.")])
    submit = SubmitField("Gửi liên kết đặt lại mật khẩu")


class ResetPasswordForm(BaseForm):
    """Form đặt mật khẩu mới từ link trong email."""
    new_password = PasswordField("Mật khẩu mới", validators=[DataRequired(), StrongPassword()])
    confirm_password = PasswordField("Nhập lại mật khẩu mới", validators=[
        DataRequired(), EqualTo("new_password", message="Mật khẩu nhập lại không khớp.")])
    submit = SubmitField("Đặt lại mật khẩu")


class ReauthForm(BaseForm):
    """Form nhập lại mật khẩu để làm mới phiên (fresh login) trước thao tác nhạy cảm."""
    password = PasswordField("Mật khẩu", validators=[DataRequired(), Length(max=LOGIN_PASSWORD_MAX)])
    next = HiddenField()
    submit = SubmitField("Xác nhận")


class ResendConfirmationForm(BaseForm):
    """Form chỉ có nút bấm (kèm CSRF) để gửi lại email xác minh."""
    submit = SubmitField("Gửi lại email xác minh")


# --------------------------------------------------------------------------
# Sự kiện (Ch8)
# --------------------------------------------------------------------------
class EventForm(BaseForm):
    """Form tạo sự kiện: tên, mã, thời gian bắt đầu/kết thúc, địa điểm, nội dung, hạn đăng ký (nằm
    trong khoảng bắt đầu – kết thúc). Sự kiện không giới hạn số người.
    """
    ten_su_kien = StringField("Tên sự kiện", filters=[_strip],
                              validators=[DataRequired(message="Vui lòng nhập tên sự kiện."),
                                          Length(max=150), NoControlChars()])
    ma_su_kien = StringField("Mã sự kiện", filters=[_strip], validators=[
        DataRequired(message="Vui lòng nhập mã sự kiện."), Length(max=30),
        Regexp(r"^[A-Za-z0-9_.\-]+$", message="Mã chỉ gồm chữ, số, dấu gạch và dấu chấm.")])
    thoi_gian_bat_dau = DateTimeLocalField("Thời gian bắt đầu", format="%Y-%m-%dT%H:%M",
                                           validators=[DataRequired(message="Thời gian bắt đầu không hợp lệ.")])
    thoi_gian_ket_thuc = DateTimeLocalField("Thời gian kết thúc", format="%Y-%m-%dT%H:%M",
                                            validators=[DataRequired(message="Thời gian kết thúc không hợp lệ.")])
    dia_diem = StringField("Địa điểm", filters=[_strip], validators=[Optional(), Length(max=300), NoControlChars()])
    noi_dung = TextAreaField("Nội dung", filters=[_strip], validators=[Optional(), Length(max=10000)])
    han_dang_ky = DateTimeLocalField("Hạn đăng ký (nếu có)", format="%Y-%m-%dT%H:%M", validators=[Optional()])
    submit = SubmitField("Tạo sự kiện")

    def validate_thoi_gian_ket_thuc(self, field):
        """Thời gian kết thúc phải sau thời gian bắt đầu."""
        if self.thoi_gian_bat_dau.data and field.data and field.data <= self.thoi_gian_bat_dau.data:
            raise ValidationError("Thời gian kết thúc phải sau thời gian bắt đầu.")

    def validate_han_dang_ky(self, field):
        """Hạn đăng ký (nếu có) phải nằm trong khoảng [bắt đầu, kết thúc] vì thời gian sự kiện đã
        gồm cả setup và báo cáo sau sự kiện.
        """
        start, end = self.thoi_gian_bat_dau.data, self.thoi_gian_ket_thuc.data
        if field.data and start and end and not start <= field.data <= end:
            raise ValidationError("Hạn đăng ký phải nằm trong khoảng thời gian bắt đầu – kết thúc sự kiện.")

    def validate_ma_su_kien(self, field):
        """Mã sự kiện không được trùng sự kiện đã có."""
        from app.models import Event
        if Event.query.filter_by(ma_su_kien=field.data).first():
            raise ValidationError("Mã sự kiện đã tồn tại, vui lòng chọn mã khác.")


# --------------------------------------------------------------------------
# Quỹ
# --------------------------------------------------------------------------
class FundPeriodForm(BaseForm):
    """Form mở kỳ quỹ mới: tên kỳ, ngày bắt đầu, ngày kết thúc (tùy chọn) và quỹ kỳ (tùy chọn).
    Nhập số tiền quỹ kỳ thì hệ thống tạo khoản thu "Quỹ <tên kỳ>" cho mọi thành viên."""
    ten_ky = StringField("Tên kỳ", filters=[_strip], validators=[
        DataRequired(message="Vui lòng nhập tên kỳ (vd: Spring 2027)."), Length(max=50), NoControlChars()])
    ngay_bat_dau = DateField("Ngày bắt đầu", validators=[DataRequired(message="Ngày không hợp lệ.")])
    ngay_ket_thuc = DateField("Ngày kết thúc", validators=[Optional()])
    so_tien_quy = StringField("Quỹ kỳ mỗi thành viên (₫)", filters=[_strip], validators=[Optional(), Length(max=32)])
    han_dong_quy = DateField("Hạn đóng quỹ kỳ", validators=[Optional()])
    submit = SubmitField("Mở kỳ mới")

    def validate_ngay_ket_thuc(self, field):
        """Ngày kết thúc (nếu có) không được trước ngày bắt đầu."""
        if field.data and self.ngay_bat_dau.data and field.data < self.ngay_bat_dau.data:
            raise ValidationError("Ngày kết thúc phải sau ngày bắt đầu.")

    def validate_so_tien_quy(self, field):
        """Số tiền quỹ kỳ (nếu nhập) phải là số dương; lưu Decimal vào `self.fund_amount`."""
        from app.services.common import DomainError, parse_money
        self.fund_amount = None
        if field.data:
            try:
                self.fund_amount = parse_money(field.data)
            except DomainError:
                raise ValidationError("Số tiền quỹ kỳ không hợp lệ.") from None

    def validate_han_dong_quy(self, field):
        """Hạn đóng quỹ kỳ không được trước ngày bắt đầu kỳ."""
        if field.data and self.ngay_bat_dau.data and field.data < self.ngay_bat_dau.data:
            raise ValidationError("Hạn đóng quỹ kỳ phải sau ngày bắt đầu kỳ.")


class FundTransactionForm(BaseForm):
    """Ghi một giao dịch thu (+) hoặc chi (số tiền bắt đầu bằng dấu -)."""

    danh_muc = StringField("Danh mục", filters=[_strip], validators=[
        DataRequired(message="Vui lòng nhập danh mục giao dịch."), Length(max=100), NoControlChars()])
    noi_dung = StringField("Nội dung", filters=[_strip], validators=[Optional(), Length(max=255), NoControlChars()])
    ngay = DateField("Ngày", validators=[DataRequired(message="Ngày giao dịch không hợp lệ.")])
    so_tien = StringField("Số tiền", filters=[_strip], validators=[
        InputRequired(message="Vui lòng nhập số tiền."), Length(max=32)])
    submit = SubmitField("Ghi giao dịch")

    def validate_so_tien(self, field):
        """Kiểm tra số tiền (cho phép dấu - với khoản chi) và lưu giá trị Decimal vào
        `self.amount`.
        """
        from app.services.common import DomainError, parse_money
        raw = field.data or ""
        try:
            amount = -parse_money(raw[1:]) if raw.startswith("-") else parse_money(raw)
        except DomainError:
            raise ValidationError("Số tiền không hợp lệ.") from None
        self.amount = amount


# --------------------------------------------------------------------------
# Hồ sơ
# --------------------------------------------------------------------------
class AvatarForm(BaseForm):
    """Upload ảnh đại diện (Ch4 – slide 52-53)."""

    avatar = FileField("Ảnh đại diện", validators=[
        FileRequired(message="Vui lòng chọn một file ảnh."),
        FileAllowed(["png", "jpg", "jpeg", "gif", "webp"],
                    message="Chỉ chấp nhận file ảnh (png, jpg, jpeg, gif, webp)."),
    ])
    submit = SubmitField("Cập nhật ảnh")
