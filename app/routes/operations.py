"""Board inbox and the authenticated member notification center."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import (
    AIProposal, EmailProposal, Event, FundCollection, FundPayment, Notification,
    Permission, Task, User,
)
from app.permissions import approved_required, permission_required
from app.security import require_csrf
from app.services.common import club_now
from app.services.email_proposals import can_review_email
from app.services.proposals import can_review

operations_bp = Blueprint("operations", __name__)

SECTION_SIZE = 10
NOTIFICATION_PAGE_SIZE = 25


def _page_rows(query, total, argument, per_page=SECTION_SIZE):
    """Return a bounded page and navigation metadata for one independent section."""
    last_page = max(1, (total + per_page - 1) // per_page)
    page = request.args.get(argument, 1, type=int) or 1
    page = min(max(page, 1), last_page)
    rows = query.offset((page - 1) * per_page).limit(per_page).all()
    return page, rows, page > 1, page < last_page


def _section(key, title, description, query, total, argument):
    page, rows, has_previous, has_next = _page_rows(query, total, argument)
    return {
        "key": key,
        "title": title,
        "description": description,
        "count": total,
        "items": rows,
        "page": page,
        "page_arg": argument,
        "has_previous": has_previous,
        "has_next": has_next,
    }


@operations_bp.get("/operations")
@login_required
@permission_required(Permission.BOARD)
def inbox():
    """Show only pending work the current board member can review."""
    sections = []

    if current_user.can(Permission.APPROVE_MEMBERS):
        query = User.query.filter_by(status="pending").order_by(User.ngay_tao.asc(), User.id.asc())
        total = query.count()
        sections.append(_section(
            "members", "Đơn đăng ký thành viên", "Hồ sơ đang chờ Ban điều hành xem xét.",
            query, total, "members_page",
        ))

    if current_user.can(Permission.MANAGE_EVENTS):
        query = (Task.query.join(Event)
                 .filter(Task.trang_thai == "cho_duyet",
                         Event.trang_thai.notin_(["cancelled", "da_ket_thuc"]))
                 .order_by(Task.deadline.asc(), Task.id.asc()))
        total = query.count()
        sections.append(_section(
            "tasks", "Sản phẩm chờ duyệt", "Nhiệm vụ đang chờ người quản lý sự kiện xem.",
            query, total, "tasks_page",
        ))

    if current_user.can(Permission.MANAGE_FUNDS):
        query = (db.session.query(FundPayment, FundCollection, User)
                 .join(FundCollection, FundCollection.id == FundPayment.fund_id)
                 .join(User, User.id == FundPayment.user_id)
                 .filter(FundPayment.trang_thai == "pending")
                 .order_by(FundPayment.ngay_dong.asc(), FundPayment.id.asc()))
        total = query.count()
        sections.append(_section(
            "payments", "Thanh toán chờ xác nhận", "Giao dịch thành viên đã gửi cho thủ quỹ.",
            query, total, "payments_page",
        ))

    if can_review(current_user, None):
        query = AIProposal.query.filter_by(status="pending").order_by(
            AIProposal.created_at.asc(), AIProposal.id.asc()
        )
        total = query.count()
        sections.append(_section(
            "ai", "Đề xuất AI chờ duyệt", "Đề xuất sự kiện hoặc chia nhiệm vụ cần được xem lại.",
            query, total, "ai_page",
        ))

    if can_review_email(current_user, None):
        query = EmailProposal.query.filter(
            EmailProposal.status.in_(["pending", "failed", "sending"])
        ).order_by(EmailProposal.created_at.asc(), EmailProposal.id.asc())
        total = query.count()
        sections.append(_section(
            "email", "Bản nháp email cần xử lý",
            "Nháp chưa gửi, gửi lỗi hoặc cần đối soát trước khi xử lý tiếp.",
            query, total, "email_page",
        ))

    return render_template(
        "operations.html",
        sections=sections,
        total_pending=sum(section["count"] for section in sections),
        now=club_now(),
    )


@operations_bp.get("/notifications")
@login_required
@approved_required
def notifications():
    """List the current user's complete notification history, newest first."""
    query = Notification.query.filter_by(user_id=current_user.id)
    total = query.count()
    last_page = max(1, (total + NOTIFICATION_PAGE_SIZE - 1) // NOTIFICATION_PAGE_SIZE)
    page = request.args.get("page", 1, type=int) or 1
    page = min(max(page, 1), last_page)
    items = (query.order_by(Notification.thoi_gian.desc(), Notification.id.desc())
             .offset((page - 1) * NOTIFICATION_PAGE_SIZE)
             .limit(NOTIFICATION_PAGE_SIZE).all())
    unread_count = query.filter_by(da_doc=False).count()
    return render_template(
        "notifications.html",
        notifications=items,
        page=page,
        last_page=last_page,
        total=total,
        unread_count=unread_count,
    )


@operations_bp.post("/notifications/<int:notification_id>/read")
@login_required
@approved_required
def notification_read(notification_id):
    """Mark one owned notification read; foreign IDs are indistinguishable from missing IDs."""
    require_csrf()
    notification = Notification.query.filter_by(
        id=notification_id, user_id=current_user.id
    ).first_or_404()
    if not notification.da_doc:
        notification.da_doc = True
        db.session.commit()
    return redirect(url_for("operations.notifications"))


@operations_bp.post("/notifications/read-all")
@login_required
@approved_required
def notifications_read_all():
    """Mark all notifications owned by the current user as read."""
    require_csrf()
    (Notification.query.filter_by(user_id=current_user.id, da_doc=False)
     .update({"da_doc": True}, synchronize_session=False))
    db.session.commit()
    flash("Đã đánh dấu tất cả thông báo là đã đọc.", "success")
    return redirect(url_for("operations.notifications"))
