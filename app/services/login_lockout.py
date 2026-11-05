"""Tạm khóa đăng nhập theo tài khoản, lưu trong database (chung cho mọi worker, không mất khi restart).

- Sai LOGIN_MAX_ATTEMPTS lần liên tiếp → khóa. Lần khóa thứ nhất LOGIN_LOCKOUT_SECONDS (mặc định
  5 phút), lần thứ hai ×3 (15 phút), từ lần thứ ba ×6 (30 phút). Khóa ngắn và tăng dần để kẻ xấu
  không khóa được tài khoản người khác quá lâu.
- Đăng nhập đúng hoặc đặt mật khẩu mới (`User.set_password`) → xóa bộ đếm và mở khóa.
- Trong lúc khóa không kiểm tra mật khẩu và không đếm thêm.
- MSSV không tồn tại không có bộ đếm; giới hạn theo IP của Flask-Limiter vẫn áp dụng.
"""
import math
from datetime import timedelta

from flask import current_app

from app import db
from app.models import User
from app.timeutil import club_now
from .common import audit

_STEPS = (1, 3, 6)


def _enabled():
    return current_app.config.get("LOGIN_LIMIT_ENABLED", True)


def _max_attempts():
    return max(1, int(current_app.config.get("LOGIN_MAX_ATTEMPTS", 5)))


def locked_minutes(user, now=None):
    """Số phút còn bị khóa (làm tròn lên), hoặc 0 nếu không bị khóa."""
    if not _enabled() or user is None or user.khoa_dang_nhap_den is None:
        return 0
    remaining = (user.khoa_dang_nhap_den - (now or club_now())).total_seconds()
    return math.ceil(remaining / 60) if remaining > 0 else 0


def record_failure(user):
    """Đếm một lần sai (UPDATE cộng dồn, an toàn khi nhiều worker); đủ ngưỡng thì khóa. Commit."""
    if not _enabled() or user is None:
        return
    db.session.execute(db.update(User).where(User.id == user.id)
                       .values(dang_nhap_sai=User.dang_nhap_sai + 1))
    db.session.refresh(user, ["dang_nhap_sai"])
    count, limit = user.dang_nhap_sai, _max_attempts()
    if count % limit == 0:
        step = _STEPS[min(count // limit, len(_STEPS)) - 1]
        seconds = max(1, int(current_app.config.get("LOGIN_LOCKOUT_SECONDS", 300))) * step
        user.khoa_dang_nhap_den = club_now() + timedelta(seconds=seconds)
        audit(None, "login_locked", user, {"failed": count, "minutes": math.ceil(seconds / 60)})
    db.session.commit()


def clear(user):
    """Xóa bộ đếm sau khi đăng nhập đúng (chưa commit)."""
    if user.dang_nhap_sai or user.khoa_dang_nhap_den:
        user.dang_nhap_sai, user.khoa_dang_nhap_den = 0, None
