"""Một nguồn thời gian duy nhất cho toàn hệ thống: giờ Việt Nam.

Mọi thời điểm được lưu, so sánh và hiển thị theo giờ CLB (`APP_TIMEZONE`, mặc định
Asia/Ho_Chi_Minh, UTC+7), dạng *naive* (không kèm múi giờ) – cùng kiểu với giờ sự kiện và
hạn task người dùng nhập. Không dùng `datetime.utcnow()` / `datetime.now()` trực tiếp.
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import current_app, has_app_context

DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"


def club_timezone():
    """Múi giờ CLB: cấu hình của app, ngoài app context thì đọc biến môi trường."""
    name = (current_app.config.get("APP_TIMEZONE") if has_app_context() else None) \
        or os.environ.get("APP_TIMEZONE") or DEFAULT_TIMEZONE
    return ZoneInfo(name)


def club_now():
    """Thời điểm hiện tại theo giờ CLB (Việt Nam), naive datetime."""
    return datetime.now(club_timezone()).replace(tzinfo=None)


def to_club_time(value):
    """Đổi datetime có múi giờ (vd. từ API bên ngoài) sang giờ CLB naive; naive giữ nguyên."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(club_timezone()).replace(tzinfo=None)
