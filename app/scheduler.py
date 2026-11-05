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
    _scheduler.add_job(func=lambda: sync_event_statuses(app), trigger="interval", minutes=5,
                       id="sync_event_statuses", replace_existing=True)
    _scheduler.start()
    app.logger.info("Scheduler started: deadline check 07:00, period reminder 07:05, "
                    "event status sync every 5 minutes")
    return _scheduler


def sync_event_statuses(app):
    """Tự chuyển trạng thái sự kiện theo giờ (xem app/services/event_status.py). Trả về số sự kiện đã đổi."""
    from app.services.event_status import sync_statuses

    with app.app_context():
        return sync_statuses()


def check_deadlines(app):
    """Tìm task sắp đến hạn và gửi mail cảnh báo cho người phụ trách. Trả về số mail đã gửi."""
    from app.models import Task  # import trễ để tránh circular import

    sent = 0
    with app.app_context():
        warn_days = app.config["DEADLINE_WARNING_DAYS"]
        target_date = (club_now() + timedelta(days=warn_days)).date()

        tasks = Task.query.filter(Task.trang_thai.in_(["dang_lam", "lam_lai"])).all()
        for task in tasks:
            if task.assignee and task.deadline.date() == target_date:
                sent += bool(send_email(task.assignee.email, *render_system_email(
                    "deadline_reminder", ten=task.assignee.ho_ten, task=task.ten_task,
                    su_kien=task.event.ten_su_kien, so_ngay=warn_days,
                    han=task.deadline.strftime('%d/%m/%Y %H:%M'))))
    return sent


def check_period_reminder(app):
    """Kỳ quỹ hiện tại sắp kết thúc mà chưa nhắc → tạo Notification + mail cho
    người có quyền quản lý quỹ. Đánh dấu `da_nhac_ky_moi` để không nhắc lặp lại.
    Trả về số người được nhắc."""
    from app.models import SYSTEM_ADMIN_ROLE, FundPeriod, Notification, Permission, User
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
        for member in managers:
            db.session.add(Notification(user_id=member.id, noi_dung=noi_dung))
            send_email(member.email, *render_system_email(
                "period_reminder", ky=period.ten_ky, so_ngay=so_ngay_con_lai,
                ngay_ket_thuc=period.ngay_ket_thuc.strftime('%d/%m/%Y')))

        period.da_nhac_ky_moi = True
        db.session.commit()
        return len(managers)
