"""Xác thực người dùng (Ch6): đăng nhập, đăng ký, đăng xuất, xác minh email,
đổi mật khẩu, quên/đặt lại mật khẩu, xác nhận lại mật khẩu (fresh login).

Nguyên tắc bảo mật áp dụng:
- Thông báo lỗi đăng nhập chung chung, không tiết lộ MSSV có tồn tại hay không.
- Khi MSSV không tồn tại vẫn chạy kiểm tra hash giả để thời gian phản hồi như nhau.
- Khóa tạm thời theo (IP, tài khoản) sau nhiều lần sai + Flask-Limiter theo IP.
- Tạo session mới (session.clear) khi đăng nhập; đăng xuất chỉ nhận POST.
- Chỉ chuyển hướng `next` tới đường dẫn nội bộ (chống open redirect).
- Link quên mật khẩu luôn trả cùng một thông báo (không lộ email nào đã đăng ký).
"""
from collections import deque
import hashlib
import hmac
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from flask import (
    Blueprint, current_app, flash, redirect, render_template, request, session, url_for,
)
from flask_login import (
    confirm_login, current_user, fresh_login_required, login_required, login_user, logout_user,
)
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from app import db
from app.forms import (
    ChangePasswordForm, ForgotPasswordForm, LoginForm, ReauthForm, RegistrationForm,
    ResendConfirmationForm, ResetPasswordForm,
)
from app.limiter import limiter
from app.mail import send_email
from app.services.email_templates import render_system_email
from app.models import Ban, User, UserBan
from app.services.access import approved
from app.services.common import audit

auth_bp = Blueprint("auth", __name__)
_MSSV_RE = re.compile(r"^[A-Za-z0-9_-]{1,20}$")
_DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))
_GENERIC_LOGIN_ERROR = "MSSV hoặc mật khẩu không đúng."
_FORGOT_PASSWORD_NOTICE = ("Nếu email thuộc một tài khoản đang hoạt động, liên kết đặt lại mật khẩu "
                           "đã được gửi. Liên kết có hiệu lực trong 1 giờ.")


def _auth_rate_limit():
    """Giới hạn tần suất cho các form xác thực, đọc từ AUTH_RATE_LIMIT."""
    return current_app.config.get("AUTH_RATE_LIMIT", "20 per minute")


class _LoginRateLimiter:
    """Khóa đăng nhập tạm thời trong RAM, khóa theo (IP, MSSV) đã được HMAC.

    Bổ sung cho Flask-Limiter: Flask-Limiter giới hạn số request theo IP, còn
    lớp này khóa một tài khoản sau LOGIN_MAX_ATTEMPTS lần sai liên tiếp.
    """

    def __init__(self):
        """Khởi tạo bảng đếm số lần sai (khóa bằng threading.Lock)."""
        self._lock = threading.Lock()
        self._attempts = {}

    def is_locked(self, key, now):
        """True nếu khóa (IP, MSSV) đang bị tạm khóa."""
        with self._lock:
            entry = self._attempts.get(key)
            if not entry:
                return False
            if entry["locked_until"] > now:
                return True
            if entry["locked_until"]:
                self._attempts.pop(key, None)
            return False

    def failed(self, key, now, *, max_attempts, window_seconds, lockout_seconds):
        """Ghi nhận một lần đăng nhập sai; đủ LOGIN_MAX_ATTEMPTS lần trong cửa sổ thời gian thì
        khóa.
        """
        cutoff = now - window_seconds
        with self._lock:
            entry = self._attempts.setdefault(key, {"timestamps": deque(), "locked_until": 0})
            timestamps = entry["timestamps"]
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            timestamps.append(now)
            if len(timestamps) >= max_attempts:
                entry["locked_until"] = now + lockout_seconds

            if len(self._attempts) > 4096:
                for stale_key, stale in list(self._attempts.items()):
                    if stale["locked_until"] <= now and not stale["timestamps"]:
                        self._attempts.pop(stale_key, None)
                while len(self._attempts) > 4096:
                    self._attempts.pop(next(iter(self._attempts)))

    def succeeded(self, key):
        """Xóa bộ đếm khi đăng nhập thành công."""
        with self._lock:
            self._attempts.pop(key, None)


def _safe_next(value):
    """Chỉ cho phép chuyển hướng tới đường dẫn nội bộ dạng "/..." (Ch6 – slide 70)."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        return None
    if "\\" in value or any(ord(char) < 32 for char in value):
        return None
    lowered = value.lower()
    if "%2f" in lowered or "%5c" in lowered:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return None
    return value


def _limiter_settings():
    """Đọc (số lần tối đa, cửa sổ, thời gian khóa) từ cấu hình."""
    cfg = current_app.config
    return (
        max(1, int(cfg.get("LOGIN_MAX_ATTEMPTS", 5))),
        max(1, int(cfg.get("LOGIN_WINDOW_SECONDS", 300))),
        max(1, int(cfg.get("LOGIN_LOCKOUT_SECONDS", 300))),
    )


def _login_limiter():
    """Lấy (hoặc tạo) bộ khóa đăng nhập dùng chung của app."""
    limiter_state = current_app.extensions.get("login_rate_limiter")
    if limiter_state is None:
        limiter_state = _LoginRateLimiter()
        current_app.extensions["login_rate_limiter"] = limiter_state
    return limiter_state


def _login_key(mssv):
    """Khóa nhận diện HMAC(IP + MSSV) – không lưu MSSV dạng rõ trong RAM."""
    address = request.remote_addr or "unknown"
    secret = str(current_app.config.get("SECRET_KEY") or "development-key")
    material = f"{address}\0{mssv.casefold()}".encode("utf-8", "replace")
    return hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()


def _login_failed(form):
    """Phản hồi đăng nhập thất bại với thông báo chung chung (không tiết lộ lý do)."""
    current_app.logger.warning("Login rejected")
    flash(_GENERIC_LOGIN_ERROR, "error")
    form.password.data = ""
    return render_template("login.html", form=form, next_target=_safe_next(form.next.data))


def _existing_session_redirect():
    """Người đã đăng nhập vào trang login/register: chuyển tới /pending hoặc /home; tài khoản không
    còn hợp lệ thì đăng xuất.
    """
    if current_user.status == "pending":
        return redirect(url_for("auth.pending"))
    if current_user.status == "approved" and approved(current_user):
        return redirect(url_for("main.home"))
    logout_user()
    session.clear()
    return None


def send_confirmation_email(user):
    """Gửi link xác minh email (token itsdangerous, hạn EMAIL_CONFIRM_MAX_AGE)."""
    token = user.generate_token("confirm")
    link = url_for("auth.confirm_email", token=token, _external=True)
    hours = current_app.config.get("EMAIL_CONFIRM_MAX_AGE", 48 * 3600) // 3600
    return send_email(user.email, *render_system_email(
        "confirm_email", ten=user.ho_ten, link=link, so_gio=hours))


def _send_reset_email(user):
    """Gửi email chứa link đặt lại mật khẩu (hiệu lực 1 giờ, dùng một lần)."""
    token = user.generate_token("reset")
    link = url_for("auth.reset_password", token=token, _external=True)
    return send_email(user.email, *render_system_email(
        "reset_password", ten=user.ho_ten, mssv=user.mssv, link=link))


# --------------------------------------------------------------------------
# Trang công khai, đăng nhập, đăng ký
# --------------------------------------------------------------------------
@auth_bp.route("/", methods=["GET"])
def index():
    """Trang giới thiệu công khai; người đã đăng nhập được chuyển vào workspace."""
    if current_user.is_authenticated:
        return redirect(url_for("main.home"))
    return render_template("landing.html")


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit(_auth_rate_limit, methods=["POST"])
def login():
    """Đăng nhập bằng LoginForm: kiểm tra khóa tạm, so khớp mật khẩu (thời gian như nhau), tạo
    session mới, hỗ trợ "ghi nhớ đăng nhập" và `next` nội bộ.
    """
    if current_user.is_authenticated:
        response = _existing_session_redirect()
        if response:
            return response

    form = LoginForm()
    if request.method == "GET":
        form.next.data = _safe_next(request.args.get("next")) or ""
        return render_template("login.html", form=form, next_target=form.next.data)

    if not form.validate_on_submit():
        return _login_failed(form)

    mssv = form.mssv.data
    password = form.password.data
    key = _login_key(mssv[:256])
    limiter_enabled = current_app.config.get("LOGIN_LIMIT_ENABLED", True)
    lockout = _login_limiter() if limiter_enabled else None
    max_attempts, window_seconds, lockout_seconds = _limiter_settings()

    if lockout and lockout.is_locked(key, time.monotonic()):
        current_app.logger.warning("Login rate limit exceeded")
        return _login_failed(form)

    valid_mssv = bool(_MSSV_RE.fullmatch(mssv))
    # MSSV lưu dạng in hoa (SE201256); tên đăng nhập Quản trị hệ thống giữ nguyên như khi tạo
    user = ((User.query.filter_by(mssv=mssv).first()
             or User.query.filter_by(mssv=mssv.upper()).first()) if valid_mssv else None)
    password_matches = (
        user.check_password(password)
        if user is not None
        else check_password_hash(_DUMMY_PASSWORD_HASH, password)
    )

    if (not valid_mssv or user is None or not password_matches
            or user.status not in {"approved", "pending"}
            or (user.status == "approved" and not approved(user))):
        if lockout:
            lockout.failed(key, time.monotonic(), max_attempts=max_attempts,
                           window_seconds=window_seconds, lockout_seconds=lockout_seconds)
        return _login_failed(form)

    if lockout:
        lockout.succeeded(key)
    session.clear()                                   # tạo session mới, chống session fixation
    login_user(user, remember=bool(form.remember.data))
    session.permanent = True
    if user.status == "pending":
        return redirect(url_for("auth.pending"))
    if user.must_change_password:
        flash("Bạn đang dùng mật khẩu tạm. Hãy đặt mật khẩu mới để tiếp tục.", "error")
        return redirect(url_for("auth.change_password"))
    return redirect(_safe_next(form.next.data) or url_for("main.home"))


@auth_bp.route("/pending")
@login_required
def pending():
    """Trang chờ duyệt cho tài khoản mới đăng ký (kèm gửi lại email xác minh)."""
    if current_user.status != "pending":
        return redirect(url_for("main.home"))
    return render_template("pending.html", resend_form=ResendConfirmationForm())


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit(_auth_rate_limit, methods=["POST"])
def register():
    """Đăng ký thành viên bằng RegistrationForm; tạo tài khoản trạng thái pending và gửi email xác
    minh.
    """
    if current_user.is_authenticated:
        response = _existing_session_redirect()
        if response:
            return response

    bans = Ban.query.order_by(Ban.id).all()
    form = RegistrationForm()
    form.ban_hoat_dong.choices = [(ban.id, ban.ten_ban) for ban in bans]

    if form.validate_on_submit():
        user = User(
            mssv=form.mssv.data,
            ho_ten=form.ho_ten.data,
            ngay_sinh=form.ngay_sinh.data,
            sdt=form.sdt.data,
            email=form.email.data,
            facebook_link=form.facebook_link.data,
            status="pending",
            chuc_vu="THANH_VIEN",
        )
        user.password = form.password.data
        try:
            db.session.add(user)
            db.session.flush()
            db.session.add(UserBan(user_id=user.id, ban_id=form.ban_hoat_dong.data))
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            current_app.logger.warning("Registration rejected by a database constraint")
            flash("Không thể hoàn tất đăng ký lúc này. MSSV hoặc email có thể vừa được dùng.", "error")
            return render_template("register.html", form=form), 409

        mailed = send_confirmation_email(user)
        flash("Đăng ký thành công! Vui lòng chờ BĐH xét duyệt.", "success")
        if mailed:
            flash("Hãy kiểm tra hộp thư để xác minh email đăng ký.", "success")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        return render_template("register.html", form=form)
    return render_template("register.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    """Đăng xuất (chỉ nhận POST) và xóa session."""
    logout_user()
    clear_remember_cookie = session.get("_remember") == "clear"
    session.clear()
    if clear_remember_cookie:
        session["_remember"] = "clear"
    flash("Bạn đã đăng xuất.", "success")
    return redirect(url_for("auth.login"))


# --------------------------------------------------------------------------
# Xác minh email (Ch6 – slide 89-103)
# --------------------------------------------------------------------------
@auth_bp.route("/confirm/<token>")
def confirm_email(token):
    """Mở link xác minh email: token hợp lệ → đánh dấu email đã xác minh."""
    user = User.from_token(token, "confirm", current_app.config.get("EMAIL_CONFIRM_MAX_AGE", 48 * 3600))
    if user is None:
        flash("Liên kết xác minh không hợp lệ hoặc đã hết hạn.", "error")
        return redirect(url_for("auth.login"))
    if not user.email_confirmed:
        user.confirm_email()
        audit(user, "email_confirmed", user)
        db.session.commit()
    flash("Email của bạn đã được xác minh. Cảm ơn bạn!", "success")
    return redirect(url_for("main.home") if current_user.is_authenticated else url_for("auth.login"))


@auth_bp.route("/confirm/resend", methods=["POST"])
@login_required
@limiter.limit("3 per hour")
def resend_confirmation():
    """Gửi lại email xác minh (giới hạn 3 lần/giờ)."""
    form = ResendConfirmationForm()
    if form.validate_on_submit():
        if current_user.email_confirmed:
            flash("Email của bạn đã được xác minh trước đó.", "success")
        elif send_confirmation_email(current_user):
            flash("Đã gửi lại email xác minh. Hãy kiểm tra hộp thư.", "success")
        else:
            flash("Chưa gửi được email. Vui lòng liên hệ BĐH.", "error")
    target = "auth.pending" if current_user.status == "pending" else "profile.view_profile"
    return redirect(url_for(target))


# --------------------------------------------------------------------------
# Đổi mật khẩu / quên mật khẩu (Ch6 – slide 104-110)
# --------------------------------------------------------------------------
@auth_bp.route("/password/change", methods=["GET", "POST"])
@login_required
@fresh_login_required
def change_password():
    """Đổi mật khẩu (cần fresh login); đổi xong mọi phiên khác bị đăng xuất, phiên hiện tại được
    cấp lại.
    """
    form = ChangePasswordForm()
    form.mssv_value = current_user.mssv
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append("Mật khẩu hiện tại không đúng.")
        elif current_user.check_password(form.new_password.data):
            form.new_password.errors.append("Mật khẩu mới phải khác mật khẩu hiện tại.")
        else:
            current_user.password = form.new_password.data
            current_user.must_change_password = False
            audit(current_user, "password_change", current_user)
            db.session.commit()
            # Phiên cũ (và cookie ghi nhớ) mất hiệu lực vì session_marker đổi → đăng nhập lại phiên hiện tại
            session.clear()
            login_user(current_user._get_current_object())
            session.permanent = True
            flash("Đã đổi mật khẩu. Các phiên đăng nhập khác đã bị đăng xuất.", "success")
            return redirect(url_for("profile.view_profile") if current_user.status == "approved"
                            else url_for("auth.pending"))
    return render_template("auth_password.html", form=form, mode="change",
                           forced=current_user.must_change_password)


@auth_bp.route("/password/forgot", methods=["GET", "POST"])
@limiter.limit(_auth_rate_limit, methods=["POST"])
def forgot_password():
    """Nhận email và gửi link đặt lại mật khẩu; luôn trả cùng một thông báo để không lộ email nào
    có tài khoản.
    """
    if current_user.is_authenticated:
        return redirect(url_for("auth.change_password"))
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        user = User.query.filter(User.email == form.email.data).first()
        if user is not None and user.status in {"approved", "pending"}:
            if not _send_reset_email(user):
                current_app.logger.warning("Password reset email could not be sent")
        # Luôn trả cùng một thông báo để không lộ email nào có tài khoản
        flash(_FORGOT_PASSWORD_NOTICE, "success")
        return redirect(url_for("auth.login"))
    return render_template("auth_password.html", form=form, mode="forgot")


@auth_bp.route("/password/reset/<token>", methods=["GET", "POST"])
@limiter.limit(_auth_rate_limit, methods=["POST"])
def reset_password(token):
    """Đặt mật khẩu mới từ link email; token hết hiệu lực ngay sau khi dùng."""
    if current_user.is_authenticated:
        return redirect(url_for("auth.change_password"))
    user = User.from_token(token, "reset", current_app.config.get("PASSWORD_RESET_MAX_AGE", 3600))
    if user is None:
        flash("Liên kết đặt lại mật khẩu không hợp lệ, đã dùng hoặc đã hết hạn.", "error")
        return redirect(url_for("auth.forgot_password"))
    form = ResetPasswordForm()
    form.mssv_value = user.mssv
    if form.validate_on_submit():
        user.password = form.new_password.data   # đổi hash → token này tự mất hiệu lực
        user.must_change_password = False
        # Mở được link trong email nghĩa là email thuộc về người dùng
        user.confirm_email()
        audit(user, "password_reset", user)
        db.session.commit()
        flash("Đã đặt lại mật khẩu. Hãy đăng nhập bằng mật khẩu mới.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth_password.html", form=form, mode="reset", token=token)


@auth_bp.route("/reauth", methods=["GET", "POST"])
@login_required
@limiter.limit(_auth_rate_limit, methods=["POST"])
def reauthenticate():
    """Xác nhận lại mật khẩu để phiên trở thành "fresh" (Ch6 – slide 115)."""
    form = ReauthForm()
    if request.method == "GET":
        form.next.data = _safe_next(request.args.get("next")) or ""
    if form.validate_on_submit():
        if current_user.check_password(form.password.data):
            confirm_login()
            return redirect(_safe_next(form.next.data) or url_for("main.home"))
        current_app.logger.warning("Re-authentication rejected")
        form.password.errors.append("Mật khẩu không đúng.")
    return render_template("auth_password.html", form=form, mode="reauth")

