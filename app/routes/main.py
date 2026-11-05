from datetime import datetime, timedelta
from decimal import Decimal

from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from sqlalchemy import func

from app import db
from app.models import (
    Attendance, Event, EventRegistration, FundPeriod, FundTransaction,
    Notification, Task,
)
from app.permissions import approved_required
from app.services.common import club_now
from app.services.access import can_manage_events, is_board, is_treasurer, visible_members
from app.services.briefings import daily_digest
from app.services.activities import ACTIVE_LEVELS, level_filter

main_bp = Blueprint("main", __name__)

TASK_STATUSES = ("dang_lam", "cho_duyet", "lam_lai", "hoan_thanh", "huy")


def _task_scope(actor, query):
    """Admins see the club-wide task board; members see only their own tasks."""
    if can_manage_events(actor):
        return query
    return query.filter(Task.assignee_id == actor.id)


def _recent_attendance_chart(now):
    """Số lượt điểm danh có mặt của 6 tháng gần nhất và đường SVG cho biểu đồ trang Tổng quan."""
    month_floor = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    months = []
    for offset in range(-5, 1):
        index = month_floor.year * 12 + month_floor.month - 1 + offset
        year, month_index = divmod(index, 12)
        month = month_index + 1
        start = datetime(year, month, 1)
        next_index = year * 12 + month_index + 1
        next_year, next_month_index = divmod(next_index, 12)
        finish = datetime(next_year, next_month_index + 1, 1)
        months.append({"start": start, "finish": finish, "label": f"Tháng {month:02d}", "value": 0})

    start = months[0]["start"]
    checkins = (
        Attendance.query.join(Attendance.registration)
        .filter(
            Attendance.checkin_luc >= start,
            Attendance.checkin_luc <= now,
            Attendance.trang_thai.in_(["on_time", "late"]),
            EventRegistration.trang_thai == "registered",
        )
        .with_entities(Attendance.checkin_luc)
        .all()
    )
    for (checked_in_at,) in checkins:
        for item in months:
            if item["start"] <= checked_in_at < item["finish"]:
                item["value"] += 1
                break

    max_value = max((item["value"] for item in months), default=0)
    points = []
    for index, item in enumerate(months):
        x = 70 + index * 120
        y = 180 if not max_value else round(180 - item["value"] / max_value * 135)
        points.append({**item, "x": x, "y": y})
    chart_path = " ".join(("M" if index == 0 else "L") + f"{point['x']} {point['y']}"
                           for index, point in enumerate(points))
    return points, chart_path, sum(item["value"] for item in months)


def _format_currency(value):
    """Định dạng số tiền kiểu 1.000.000 ₫."""
    formatted = f"{Decimal(str(value or 0)):,.2f}"
    whole, fraction = formatted.split(".")
    fraction = fraction.rstrip("0")
    return whole.replace(",", ".") + ("," + fraction if fraction else "") + " ₫"


@main_bp.route("/home")
@login_required
@approved_required
def home():
    """Trang Tổng quan: thông báo, sự kiện sắp tới, số liệu thành viên/task, số dư quỹ (người quản
    lý quỹ), biểu đồ điểm danh.
    """
    now = club_now()
    # Cot trai: thong bao (task chua hoan thanh, su kien/task cua CLB,...)
    notifications = (
        Notification.query.filter_by(user_id=current_user.id)
        .order_by(Notification.thoi_gian.desc())
        .limit(20)
        .all()
    )

    # Cot phai: su kien sap dien ra + nguoi tham gia/duoc giao task
    upcoming_events = (
        Event.query.filter(Event.trang_thai.in_(["sap_dien_ra", "dang_dien_ra"]),
                           Event.thoi_gian_ket_thuc >= club_now())
        .order_by(Event.thoi_gian_bat_dau.asc())
        .limit(5)
        .all()
    )
    event_participants = {}
    for ev in upcoming_events:
        assignees = {t.assignee for t in ev.tasks if t.assignee is not None}
        event_participants[ev.id] = list(assignees)

    visible_member_query = visible_members(current_user)
    active_members = visible_member_query.filter(level_filter(ACTIVE_LEVELS)).count()
    year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    next_year = year_start.replace(year=year_start.year + 1)
    events_this_year = Event.query.filter(
        Event.thoi_gian_bat_dau >= year_start,
        Event.thoi_gian_bat_dau < next_year,
    ).count()
    task_query = _task_scope(current_user, Task.query)
    completed_tasks = task_query.filter(Task.trang_thai == "hoan_thanh").count()
    upcoming_deadline_tasks = task_query.filter(
        Task.trang_thai.in_(["dang_lam", "cho_duyet", "lam_lai"]),
        Task.deadline >= now,
        Task.deadline < now + timedelta(days=7),
    ).count()

    fund_balance_label = None
    if is_treasurer(current_user):
        current_period = FundPeriod.query.filter_by(is_current=True).first()
        if current_period:
            balance = db.session.query(func.coalesce(func.sum(FundTransaction.so_tien), 0)).filter_by(
                period_id=current_period.id
            ).scalar()
        else:
            balance = 0
        fund_balance_label = _format_currency(balance)

    attendance_months, attendance_chart_path, attendance_total = _recent_attendance_chart(now)

    return render_template(
        "home.html",
        notifications=notifications,
        upcoming_events=upcoming_events,
        event_participants=event_participants,
        current_time=now,
        daily_digest=daily_digest(current_user) if is_board(current_user) else None,
        active_members=active_members,
        visible_member_count=visible_member_query.count(),
        events_this_year=events_this_year,
        completed_tasks=completed_tasks,
        upcoming_deadline_tasks=upcoming_deadline_tasks,
        fund_balance_label=fund_balance_label,
        attendance_months=attendance_months,
        attendance_chart_path=attendance_chart_path,
        attendance_total=attendance_total,
    )


@main_bp.get("/tasks")
@login_required
@approved_required
def tasks():
    """Bảng công việc: người quản lý sự kiện thấy mọi task, thành viên chỉ thấy task của mình; lọc
    theo trạng thái, xem dạng bảng/danh sách.
    """
    scoped_tasks = _task_scope(current_user, Task.query).order_by(Task.deadline.asc(), Task.id.asc()).all()
    counts = {status: 0 for status in TASK_STATUSES}
    for task in scoped_tasks:
        if task.trang_thai in counts:
            counts[task.trang_thai] += 1

    requested_status = request.args.get("status", "all")
    selected_status = requested_status if requested_status in TASK_STATUSES else "all"
    selected_view = "list" if request.args.get("view") == "list" else "board"
    filtered_tasks = [task for task in scoped_tasks
                      if selected_status == "all" or task.trang_thai == selected_status]

    return render_template(
        "newtasks.html",
        tasks=filtered_tasks,
        task_counts=counts,
        total_tasks=len(scoped_tasks),
        selected_status=selected_status,
        selected_view=selected_view,
        can_manage_events=can_manage_events(current_user),
    )
