"""Helpers for durable in-app notifications."""

import hashlib

from app import db


def create_notification(user_id, message, dedup_key=None):
    """Queue a notification in the current transaction.

    A caller may supply a stable dedup_key when repeated work should result in
    one notification. The marker and notification share the caller's
    transaction, so a rollback removes both.
    """
    from app.models import AuditLog, Notification

    if not user_id:
        return None
    message = str(message or "").strip()
    if not message:
        return None

    if dedup_key is not None:
        marker_id = hashlib.sha256(str(dedup_key).encode("utf-8")).hexdigest()
        marker_query = AuditLog.query.filter_by(
            action="notification_dedup",
            object_type="Notification",
            object_id=marker_id,
        )
        if marker_query.first() is not None:
            return None
        db.session.add(AuditLog(
            actor_id=None,
            action="notification_dedup",
            object_type="Notification",
            object_id=marker_id,
            details={},
        ))

    notification = Notification(
        user_id=user_id,
        noi_dung=message[:255],
        da_doc=False,
    )
    db.session.add(notification)
    return notification


def notify_task_action(action, task, details=None):
    """Queue audience-specific notifications for audited task changes."""
    from app.models import User

    details = details or {}
    event = getattr(task, "event", None)
    event_name = getattr(event, "ten_su_kien", None) or "sự kiện"
    task_name = getattr(task, "ten_task", None) or "nhiệm vụ"
    task_label = f'"{task_name}" trong sự kiện "{event_name}"'

    if action in {"task_assigned", "task_reassigned"}:
        assignee_id = getattr(task, "assignee_id", None)
        create_notification(
            assignee_id,
            f"Bạn được giao nhiệm vụ {task_label}.",
        )
        old_assignee_id = details.get("old_assignee_id")
        if old_assignee_id and old_assignee_id != assignee_id:
            new_assignee = db.session.get(User, assignee_id) if assignee_id else None
            recipient = new_assignee.ho_ten if new_assignee else "thành viên khác"
            create_notification(
                old_assignee_id,
                f"Nhiệm vụ {task_label} đã được giao lại cho {recipient}.",
            )
        return

    if action == "task_submitted":
        reviewer_id = getattr(event, "tao_boi_id", None)
        assignee = db.session.get(User, getattr(task, "assignee_id", None))
        submitter = assignee.ho_ten if assignee else "Thành viên"
        create_notification(
            reviewer_id,
            f"{submitter} đã nộp sản phẩm cho nhiệm vụ {task_label}; vui lòng xem xét.",
        )
        return

    if action == "task_reviewed":
        status_labels = {
            "lam_lai": "cần làm lại",
            "hoan_thanh": "đã hoàn thành",
            "huy": "đã hủy",
        }
        status_label = status_labels.get(details.get("status"), "đã được cập nhật")
        create_notification(
            getattr(task, "assignee_id", None),
            f"Nhiệm vụ {task_label} {status_label}. Mở trang sự kiện để xem chi tiết.",
        )
