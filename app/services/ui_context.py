"""Biến dùng chung cho mọi template (context processor – Ch8 slide 149).

Không tạo dữ liệu mẫu và không giả lập vai trò.
"""
from flask import request
from flask_login import current_user

from app.models import Notification, Permission
from app.services.access import approved, has_permission
from app.services.common import club_now

PAGE_LABELS = {
    "main.home": ("Tổng quan câu lạc bộ", "Theo dõi và phối hợp hoạt động FIA."),
    "main.tasks": ("Công việc", "Cùng theo dõi các đầu việc của câu lạc bộ."),
    "member": ("Thành viên", "Kết nối, quản lý và phát triển đội ngũ FIA."),
    "event": ("Sự kiện FIA", "Cùng nhau tạo nên những hoạt động đáng nhớ."),
    "funds": ("Quỹ câu lạc bộ", "Minh bạch khoản thu, chi và tình trạng đóng quỹ."),
    "ai": ("AI Agent", "Trợ lý cho công việc câu lạc bộ, có kiểm chứng và phê duyệt."),
    "ai.message_sources": ("Nguồn kiểm chứng AI", "Dữ liệu tại thời điểm trả lời; kiểm tra lại quyền khi mở."),
    "admin.approve_list": ("Phê duyệt thành viên", "Chào đón những thành viên mới của FIA."),
    "admin.role_list": ("Vai trò & phân quyền", "Phối hợp và phân công trách nhiệm trong câu lạc bộ."),
    "admin.issue_temp_password": ("Mật khẩu tạm", "Chỉ hiển thị một lần."),
    "operations.inbox": ("Hộp thư điều hành", "Các việc đang chờ trong phạm vi quyền của bạn."),
    "operations.notifications": ("Thông báo", "Lịch sử cập nhật dành riêng cho tài khoản của bạn."),
    "auth.change_password": ("Đổi mật khẩu", "Bảo vệ tài khoản của bạn."),
    "auth.reauthenticate": ("Xác nhận lại mật khẩu", "Cần cho thao tác nhạy cảm."),
    "profile": ("Hồ sơ cá nhân", "Thông tin và hoạt động của thành viên."),
    "kho_mau": ("Kho mẫu", "Mẫu email, mẫu sự kiện và mẫu chia task cho AI."),
}


def workspace_context():
    """Biến chung cho mọi template: tiêu đề trang, ngày giờ CLB, thông báo, `Permission` và hàm
    `can(perm)`.
    """
    endpoint = request.endpoint or ""
    title, subtitle = PAGE_LABELS.get(endpoint, PAGE_LABELS.get(endpoint.split(".")[0],
                                                                ("FIA Workspace", "")))
    is_approved = current_user.is_authenticated and approved(current_user)
    notification_query = Notification.query.filter_by(user_id=current_user.id) if is_approved else None
    notifications = (notification_query.order_by(
        Notification.thoi_gian.desc(), Notification.id.desc()
    ).limit(8).all()) if notification_query is not None else []
    unread_count = notification_query.filter_by(da_doc=False).count() if notification_query is not None else 0
    now = club_now()
    return {"workspace_title": title, "workspace_subtitle": subtitle,
            "workspace_today": now, "current_time": now, "shell_notifications": notifications,
            "shell_unread": unread_count,
            # Cho phép template viết: {% if can(Permission.MANAGE_FUNDS) %}
            "Permission": Permission,
            "can": lambda perm: has_permission(current_user, perm)}
