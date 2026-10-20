"""Job định kỳ bằng APScheduler.

- `check_deadlines`: mỗi ngày 07:00 gửi mail cho người phụ trách task còn đúng
  DEADLINE_WARNING_DAYS ngày (mặc định 7) là đến hạn.
- `check_period_reminder`: mỗi ngày 07:05 nhắc người quản lý quỹ khi kỳ quỹ hiện
  tại sắp kết thúc (trong PERIOD_REMINDER_DAYS ngày) để mở kỳ kế tiếp.

Hai cách chạy:
1. Trong tiến trình web (mặc định, SCHEDULER_IN_WEB=true) – hợp với 1 process
   (`python run.py` hoặc Gunicorn 1 worker).
2. Tiến trình riêng (`flask --app run.py run-scheduler`) và đặt
   SCHEDULER_IN_WEB=false cho web – BẮT BUỘC khi Gunicorn chạy nhiều worker,
   nếu không mỗi worker sẽ chạy job một lần → gửi mail trùng (Ch11).
"""
import os
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from app import db
from app.mail import send_email
from app.services.email_templates import render_system_email
from app.services.common import club_now
from app.services.notifications import create_notification

_scheduler = None


def init_scheduler(app, force=False):
    """Khởi động scheduler (một lần cho mỗi process)."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    # Flask debug reloader chạy app 2 lần → chỉ chạy ở tiến trình con thật
    if not force and app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return None

    _scheduler = BackgroundScheduler(daemon=True, timezone=app.config["APP_TIMEZONE"])
    _scheduler.add_job(func=lambda: check_deadlines(app), trigger="cron", hour=7, minute=0,
                       id="check_deadlines", replace_existing=True)
    _scheduler.add_job(func=lambda: check_period_reminder(app), trigger="cron", hour=7, minute=5,
                       id="check_period_reminder", replace_existing=True)
    if app.config.get("BACKUP_ENABLED", True):
        from app.services.maintenance import run_daily_backup
        _scheduler.add_job(
            func=lambda: run_daily_backup(app),
            trigger="cron",
            hour=2,
            minute=0,
            id="daily_database_backup",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    _scheduler.add_job(
        func=lambda: purge_expired_email_bodies(app),
        trigger="cron",
        hour=2,
        minute=15,
        id="purge_expired_email_delivery_bodies",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    app.logger.info("Scheduler started: deadline check 07:00, period reminder 07:05")
    return _scheduler


def check_deadlines(app):
    """Tạo thông báo và gửi mail cho task sắp hạn; dedupe thông báo theo task/người/ngày."""
    from app.models import Event, Task

    sent = 0
    with app.app_context():
        warn_days = app.config["DEADLINE_WARNING_DAYS"]
        today = club_now().date()
        target_date = today + timedelta(days=warn_days)

        tasks = (Task.query.join(Event)
                 .filter(Task.trang_thai.in_(["dang_lam", "lam_lai"]),
                         Event.trang_thai.notin_(["cancelled", "da_ket_thuc"]))
                 .all())
        mail_requests = []
        for task in tasks:
            if task.assignee and task.deadline.date() == target_date:
                notification = create_notification(
                    task.assignee_id,
                    f'Nhiệm vụ "{task.ten_task}" trong sự kiện "{task.event.ten_su_kien}" '
                    f'có hạn {task.deadline.strftime("%d/%m/%Y %H:%M")} '
                    f'(còn {warn_days} ngày).',
                    dedup_key=f"deadline:{today.isoformat()}:{task.id}:{task.assignee_id}",
                )
                if notification is None:
                    continue
                mail_requests.append((
                    task.assignee.email,
                    render_system_email(
                        "deadline_reminder", ten=task.assignee.ho_ten, task=task.ten_task,
                        su_kien=task.event.ten_su_kien, so_ngay=warn_days,
                        han=task.deadline.strftime('%d/%m/%Y %H:%M')),
                ))
        db.session.commit()
        for recipient, message in mail_requests:
            sent += bool(send_email(recipient, *message))
    return sent


def purge_expired_email_bodies(app):
    """Run the bounded-retention cleanup daily with an application context."""
    from app.services.mail_delivery import purge_expired_delivery_bodies

    with app.app_context():
        expired = purge_expired_delivery_bodies()
        if expired:
            app.logger.info("Expired email delivery bodies removed: %s", expired)
        return expired


def check_period_reminder(app):
    """Kỳ quỹ hiện tại sắp kết thúc mà chưa nhắc → tạo Notification + mail cho
    người có quyền quản lý quỹ. Đánh dấu `da_nhac_ky_moi` để không nhắc lặp lại.
    Trả về số người được nhắc."""
    from app.models import SYSTEM_ADMIN_ROLE, FundPeriod, Permission, User
    from app.services.access import has_permission

    with app.app_context():
        period = FundPeriod.query.filter_by(is_current=True).first()
        if not period or not period.ngay_ket_thuc or period.da_nhac_ky_moi:
            return 0

        so_ngay_con_lai = (period.ngay_ket_thuc - club_now().date()).days
        if so_ngay_con_lai > app.config["PERIOD_REMINDER_DAYS"]:
            return 0

        managers = [user for user in User.query.filter(User.status == "approved",
                                                       User.chuc_vu != SYSTEM_ADMIN_ROLE).all()
                    if has_permission(user, Permission.MANAGE_FUNDS)]
        noi_dung = (
            f"Kỳ \"{period.ten_ky}\" sẽ kết thúc vào {period.ngay_ket_thuc.strftime('%d/%m/%Y')} "
            f"(còn {so_ngay_con_lai} ngày). Vui lòng chuẩn bị nhập thông tin cho kỳ kế tiếp."
        )
        mail_requests = []
        for member in managers:
            create_notification(member.id, noi_dung)
            mail_requests.append((
                member.email,
                render_system_email(
                    "period_reminder", ky=period.ten_ky, so_ngay=so_ngay_con_lai,
                    ngay_ket_thuc=period.ngay_ket_thuc.strftime('%d/%m/%Y')),
            ))

        period.da_nhac_ky_moi = True
        db.session.commit()
        for recipient, message in mail_requests:
            send_email(recipient, *message)
        return len(managers)
