"""Decorator phân quyền dùng chung cho mọi blueprint (Ch7 – slide 58-87).

Thứ tự đặt decorator (từ trên xuống) luôn là::

    @bp.route(...)
    @login_required            # 1. đã đăng nhập chưa? (401 → trang đăng nhập)
    @permission_required(...)  # 2. có quyền không? (403)
    def view(): ...

`functools.wraps` giữ nguyên tên hàm view để `url_for()` và endpoint không bị đổi.
"""
from functools import wraps

from flask import abort
from flask_login import current_user

from app.models import Permission
from app.services.access import (
    approved, can_assign_role_to, has_permission, is_board,
)


def approved_required(view_func):
    """Chỉ cho phép thành viên đã được BĐH duyệt (và chưa rời CLB) truy cập."""

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        if not approved(current_user):
            abort(403)
        return view_func(*args, **kwargs)

    return wrapped


def permission_required(permission):
    """Decorator 3 lớp: chỉ người có bit quyền `permission` mới vào được view.

    Ví dụ: ``@permission_required(Permission.MANAGE_FUNDS)``
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated or not has_permission(current_user, permission):
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def any_permission_required(*permissions):
    """Vào được nếu có ÍT NHẤT MỘT trong các quyền đã liệt kê."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated or not any(
                    has_permission(current_user, perm) for perm in permissions):
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def bdh_required(view_func):
    """Chỉ thành viên Ban điều hành (quyền BOARD) đã được duyệt."""
    return permission_required(Permission.BOARD)(view_func)


def admin_required(view_func):
    """Người có quyền phân vai trò (CN/PCN theo bảng mặc định)."""
    return permission_required(Permission.ASSIGN_ROLES)(view_func)


def role_required(*roles):
    """Chỉ đúng các chức vụ được liệt kê. Ví dụ: ``@role_required('CN', 'PCN')``.

    Ưu tiên dùng `permission_required`; decorator này dành cho quy tắc gắn với
    chức danh cụ thể.
    """

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if (not current_user.is_authenticated or not approved(current_user)
                    or current_user.chuc_vu not in roles):
                abort(403)
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def can_assign_role(assigner, target, new_role, current_counts):
    """Kiểm tra `assigner` có được chỉ định `new_role` cho `target` không.

    Trả về (True, "") hoặc (False, "lý do").
    """
    from app.config import Config
    from app.models import DEFAULT_ROLES

    if new_role not in DEFAULT_ROLES:
        return False, "Chức vụ không hợp lệ."
    if not approved(assigner) or not approved(target) or assigner.id == target.id:
        return False, "Chỉ được chỉ định cho thành viên khác đã được duyệt."
    if new_role == "ADMIN" or target.chuc_vu == "ADMIN":
        return False, "Không thể gán hoặc thay đổi vai trò Quản trị hệ thống."
    if new_role == "CN" and assigner.chuc_vu != "ADMIN":
        return False, "Vui lòng dùng quy trình nhường quyền để chỉ định Chủ nhiệm."
    if not can_assign_role_to(assigner, target, new_role):
        return False, "Bạn không có quyền phân vai trò này (chỉ gán vai trò thấp hơn cấp của bạn)."

    limit = Config.ROLE_LIMITS.get(new_role)
    if limit is not None and current_counts.get(new_role, 0) >= limit:
        return False, f"Đã đủ số lượng tối đa cho chức vụ này ({limit} người)."

    return True, ""


__all__ = [
    "Permission", "admin_required", "any_permission_required", "approved_required",
    "bdh_required", "can_assign_role", "is_board", "permission_required", "role_required",
]
