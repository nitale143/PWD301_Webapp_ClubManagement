"""Deterministic, permission-scoped club briefings and report snapshots."""

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from zoneinfo import ZoneInfo
import re

from openpyxl import Workbook
from flask import current_app

from app import db
from app.models import (AIReportSnapshot, Attendance, EmailProposal,
                        Event, EventRegistration, FundCollection,
                        FundPayment, Task)
from .access import board_members as visible_members, can_manage_events, is_board, require
from .assistant import _normalize
from .common import DomainError, audit, club_now
from .email_proposals import _validate_address, can_review_email
from .export_security import safe_excel_row
from .event_drafts import visible_drafts


def _event_scope(actor, query):
    """Phạm vi sự kiện cho báo cáo (hiện là toàn CLB)."""
    return query


def _task_scope(actor, query):
    """Phạm vi task cho báo cáo (hiện là toàn CLB)."""
    return query


def daily_digest(actor, today=None):
    """Same date + same database state yields the same text; no LLM involved."""
    require(is_board(actor))
    today = today or club_now().date()
    week_end = today + timedelta(days=7)
    task_query = _task_scope(actor, Task.query.filter(
        Task.trang_thai.in_(["dang_lam", "cho_duyet", "lam_lai"]),
        Task.deadline < datetime.combine(week_end, time.min)))
    tasks = task_query.order_by(Task.deadline, Task.id).all()
    overdue = [task for task in tasks if task.deadline.date() < today]
    due_soon = [task for task in tasks if today <= task.deadline.date() < week_end]
    events = _event_scope(actor, Event.query.filter(
        Event.trang_thai.notin_(["cancelled", "da_ket_thuc"]),
        Event.thoi_gian_bat_dau >= datetime.combine(today, time.min),
        Event.thoi_gian_bat_dau < datetime.combine(week_end, time.min))
        ).order_by(Event.thoi_gian_bat_dau, Event.id).all()
    funds = FundCollection.query.filter(
        FundCollection.trang_thai == "open", FundCollection.han_dong < today
    ).order_by(FundCollection.han_dong, FundCollection.id).all()
    drafts = visible_drafts(actor).count() if can_manage_events(actor) else 0
    emails = [row for row in EmailProposal.query.filter(
        EmailProposal.status.in_(["pending", "failed", "sending"])).order_by(EmailProposal.id)
              if can_review_email(actor, row)]
    lines = [f"Việc cần xử lý ngày {today:%d/%m/%Y} (phạm vi quyền của bạn):",
             f"- Task quá hạn: {len(overdue)}; sắp hạn trong 7 ngày: {len(due_soon)}.",
             f"- Sự kiện trong 7 ngày tới: {len(events)}.",
             f"- Khoản thu đang mở đã quá hạn: {len(funds)}.",
             f"- Bản nháp sự kiện đang dở: {drafts}; nháp email cần xử lý: {len(emails)}."]
    lines.extend(f"  Task #{task.id}: {task.ten_task} — hạn {task.deadline:%d/%m %H:%M}"
                 for task in (overdue + due_soon)[:5])
    lines.extend(f"  Sự kiện #{event.id}: {event.ten_su_kien} — {event.thoi_gian_bat_dau:%d/%m %H:%M}"
                 for event in events[:3])
    lines.append("Nguồn: task, event, fund_collection, event_draft, email_proposal trong database CLB.")
    return "\n".join(lines)


def _window(period, today):
    """Khoảng ngày của báo cáo tuần (từ thứ Hai) hoặc tháng (từ ngày 1) đến hết hôm nay."""
    if period == "week":
        start = today - timedelta(days=today.weekday())
    elif period == "month":
        start = today.replace(day=1)
    else:
        raise DomainError("Chỉ hỗ trợ báo cáo tuần hoặc tháng.")
    return start, today + timedelta(days=1)


def report_data(actor, period, today=None):
    """Số liệu báo cáo tuần/tháng (sự kiện, task, điểm danh, thanh toán) kèm nguồn – chỉ BĐH."""
    require(is_board(actor))
    today = today or club_now().date()
    start, end = _window(period, today)
    first, last = datetime.combine(start, time.min), datetime.combine(end, time.min)
    club_zone = ZoneInfo(current_app.config["APP_TIMEZONE"])
    payment_first = first.replace(tzinfo=club_zone).astimezone(timezone.utc).replace(tzinfo=None)
    payment_last = last.replace(tzinfo=club_zone).astimezone(timezone.utc).replace(tzinfo=None)
    visible_ids = {member.id for member in visible_members(actor)}
    events = _event_scope(actor, Event.query.filter(
        Event.trang_thai == "da_ket_thuc", Event.thoi_gian_ket_thuc >= first,
        Event.thoi_gian_ket_thuc < last)).order_by(Event.id).all()
    tasks = _task_scope(actor, Task.query.filter(
        Task.deadline >= first, Task.deadline < last,
        Task.trang_thai != "huy")).order_by(Task.id).all()
    event_ids = [event.id for event in events]
    attendance = (Attendance.query.join(EventRegistration)
                  .filter(EventRegistration.event_id.in_(event_ids),
                          EventRegistration.user_id.in_(visible_ids),
                          Attendance.trang_thai.in_(["on_time", "late", "absent", "excused"]))
                  .order_by(Attendance.id).all()) if event_ids else []
    payments = (FundPayment.query.filter(
        FundPayment.trang_thai == "confirmed", FundPayment.xac_nhan_luc >= payment_first,
        FundPayment.xac_nhan_luc < payment_last, FundPayment.user_id.in_(visible_ids))
        .order_by(FundPayment.id).all())
    full_scope = is_board(actor)
    return {"period": period, "from": start.isoformat(), "through": today.isoformat(),
            "scope": "toàn CLB" if full_scope else "ban bạn quản lý",
            "scope_level": "club" if full_scope else "ban",
            "scope_ban_ids": [] if full_scope else sorted(actor.quan_ly_ban_ids()),
            "events": [{"id": row.id, "name": row.ten_su_kien,
                        "ended_at": row.thoi_gian_ket_thuc.isoformat()} for row in events],
            "tasks": [{"id": row.id, "name": row.ten_task, "event_id": row.event_id,
                       "ban_id": row.ban_id, "deadline": row.deadline.isoformat(),
                       "status": row.trang_thai} for row in tasks],
            "attendance": [{"id": row.id, "event_id": row.registration.event_id,
                            "member_id": row.registration.user_id,
                            "status": row.trang_thai} for row in attendance],
            "payments": [{"id": row.id, "fund_id": row.fund_id,
                          "member_id": row.user_id, "amount": str(row.so_tien),
                          "confirmed_at_utc": row.xac_nhan_luc.isoformat()} for row in payments]}


def report_text(payload):
    """Diễn giải payload báo cáo thành văn bản tiếng Việt."""
    label = "tuần" if payload["period"] == "week" else "tháng"
    events, tasks = payload["events"], payload["tasks"]
    attendance, payments = payload["attendance"], payload["payments"]
    present = sum(row["status"] in {"on_time", "late"} for row in attendance)
    done = sum(row["status"] == "hoan_thanh" for row in tasks)
    amount = sum((Decimal(row["amount"]) for row in payments), Decimal("0.00"))
    def ids(rows):
        values = ", ".join(str(row["id"]) for row in rows[:20]) or "không có"
        return values + (f" … (còn {len(rows) - 20})" if len(rows) > 20 else "")
    return (f"Báo cáo {label} {payload['from']}–{payload['through']} ({payload['scope']}):\n"
            f"{len(events)} sự kiện, {present} lượt có mặt; "
            f"{len(tasks)} task có hạn trong kỳ ({done} hiện đã hoàn thành).\n"
            f"Khoản thu mới xác nhận trong kỳ: {len(payments)} giao dịch, {amount:,.0f} đ. "
            "Không gồm quỹ kỳ kiểu cũ.\n"
            f"Nguồn DB — event ID: {ids(events)}; task ID: {ids(tasks)}; "
            f"attendance ID: {ids(attendance)}; fund_payment ID: {ids(payments)}.\n"
            "Số liệu là trạng thái tại lúc lập; xuất/gửi từ bản đã lưu để giữ nguyên nguồn.")


def save_report(actor, period):
    """Lưu bản chụp báo cáo (bỏ qua nếu giống hệt một bản gần đây)."""
    payload = report_data(actor, period)
    previous = (AIReportSnapshot.query.filter_by(user_id=actor.id, period=period)
                .order_by(AIReportSnapshot.id.desc()).limit(5).all())
    for row in previous:
        if row.payload == payload:
            return row
    snapshot = AIReportSnapshot(user_id=actor.id, period=period, payload=payload)
    db.session.add(snapshot)
    db.session.flush()
    audit(actor, "ai_report_saved", snapshot, {"period": period})
    db.session.commit()
    return snapshot


def can_access_report(actor, snapshot):
    """Recheck current permissions; an old snapshot must not outlive its scope."""
    if not is_board(actor) or snapshot.user_id != actor.id:
        return False
    payload = snapshot.payload or {}
    return payload.get("scope_level") in {"club", "ban"}


def report_workbook(snapshot):
    """Tạo file Excel từ bản chụp báo cáo (đã chống chèn công thức)."""
    payload = snapshot.payload
    wb = Workbook()
    overview = wb.active
    overview.title = "Tổng quan"
    overview.append(safe_excel_row(["Bản xem trước", snapshot.id]))
    for line in report_text(payload).splitlines():
        overview.append(safe_excel_row([line]))
    for name, rows, columns in (
        ("Sự kiện", payload["events"], ("id", "name", "ended_at")),
        ("Task", payload["tasks"], ("id", "name", "event_id", "ban_id", "deadline", "status")),
        ("Điểm danh", payload["attendance"], ("id", "event_id", "member_id", "status")),
        ("Thu quỹ mới", payload["payments"], ("id", "fund_id", "member_id", "amount", "confirmed_at_utc")),
    ):
        sheet = wb.create_sheet(name)
        sheet.append(safe_excel_row(columns))
        for row in rows:
            sheet.append(safe_excel_row([row[key] for key in columns]))
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def report_email_draft(actor, snapshot):
    """Tạo nháp email báo cáo gửi về email hồ sơ của chính người dùng."""
    require(can_access_report(actor, snapshot))
    address = _validate_address(actor.email)
    if address.lower().endswith(("@example.com", "@example.test")):
        raise DomainError("Email hồ sơ của bạn chưa phải địa chỉ nhận thật.")
    text = report_text(snapshot.payload)
    draft = EmailProposal(recipient_id=actor.id, recipient_email=address,
                          request_text=f"Báo cáo đã lưu #{snapshot.id}",
                          subject=f"Báo cáo CLB {snapshot.payload['period']} đến {snapshot.payload['through']}",
                          body=text[:3000], status="pending", created_by_id=actor.id)
    db.session.add(draft)
    db.session.flush()
    audit(actor, "ai_report_email_draft", draft, {"report_id": snapshot.id})
    db.session.commit()
    return draft


def briefing_kind(message):
    """Nhận diện câu hỏi tóm tắt hôm nay (daily), báo cáo tuần/tháng, hoặc mốc chưa hỗ trợ."""
    text = _normalize(message).strip().rstrip(".!?")
    if any(phrase in text for phrase in ("hom nay can", "viec can lam", "tom tat hom nay",
                                       "lich hom nay", "can xu ly gi")):
        if re.search(r"\b20\d{2}\b|\b\d{1,2}/\d{1,2}\b|\b(?:ngay mai|hom qua)\b", text):
            return "unsupported"
        return "daily"
    prefix = r"(?:(?:hay|giup|cho toi|cho tui|xem|lap|tao|soan|tong hop)\s+)*bao cao"
    if re.match(r"^" + prefix + r"\b", text):
        supported = re.fullmatch(prefix + r"\s+(?:theo\s+)?(tuan|thang)"
                                 r"(?:\s+(?:nay|hien tai))?"
                                 r"(?:\s+(?:cua clb|cua toi|cho toi|cho tui|giup toi|giup tui))*", text)
        if supported:
            return "week" if supported.group(1) == "tuan" else "month"
        return "unsupported"
    return None


def maybe_answer_briefing(actor, message):
    """Trả lời câu hỏi tóm tắt/báo cáo nếu nhận diện được; ngược lại trả None."""
    kind = briefing_kind(message)
    if kind == "unsupported":
        require(is_board(actor))
        return {"intent": "unsupported_query", "provider": "database",
                "verification": "unsupported", "answer":
                "Tóm tắt hiện hỗ trợ hôm nay; báo cáo hỗ trợ tuần này hoặc tháng này đến hôm nay. "
                "Tôi chưa lọc được kỳ bạn yêu cầu; hãy chọn một trong các khoảng này."}
    if kind == "daily":
        require(is_board(actor))
        return {"intent": "daily_digest", "provider": "database",
                "verification": "database",
                "answer": daily_digest(actor)}
    if kind in {"week", "month"}:
        require(is_board(actor))
        return {"intent": "verified_report", "preview": True, "provider": "database",
                "verification": "database",
                "answer": report_text(report_data(actor, kind))}
    return None
