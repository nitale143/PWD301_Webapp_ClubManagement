"""Bằng chứng từ công cụ ORM có sẵn; không tin nguồn hoặc URL do model tự bịa."""
from hashlib import sha256

from flask import url_for

from app import db
from app.evidence_models import ChatEvidence
from app.models import (ChatMessage, Event, FundCollection, FundDue, FundPayment,
                        FundPeriod, Task, User, role_permissions)
from .access import (approved, board_members, can_manage_events, can_manage_funds,
                     can_manage_members, is_board, require)
from .common import DomainError


QUERY_LABELS = {
    "member_info": "Hồ sơ cơ bản và liên kết ban hiện tại của thành viên được phép xem",
    "member_tasks": "Task của thành viên được chọn và bộ lọc trạng thái trong câu hỏi",
    "partial_payments": "Khoản thu đang mở; tiền đã xác nhận và số còn phải đóng theo người",
    "unpaid_members": "Kỳ quỹ được chọn/hiện tại và khoản thu đang mở, trong phạm vi người hỏi",
    "fund_overview": "Tình trạng đóng quỹ theo kỳ và số khoản thu đang mở trong phạm vi người hỏi",
    "collected_total": "Tổng FundPayment có trạng thái confirmed; không cộng khoản thu chưa xác nhận",
    "inactive_members": "Hoạt động thành viên theo quy tắc hệ thống hoặc cửa sổ 60 ngày được yêu cầu",
    "highest_absence": "Số bản ghi điểm danh absent được nhóm theo sự kiện",
    "monthly_report": "Sự kiện và lượt có mặt trong 30 ngày gần nhất tới thời điểm tra cứu",
    "upcoming_events": "20 sự kiện gần nhất có giờ bắt đầu từ hiện tại, không cancelled/draft",
    "daily_digest": "Tổng hợp việc cần xử lý hôm nay bằng các truy vấn đã định nghĩa",
    "verified_report": "Báo cáo tuần/tháng tới hiện tại trong phạm vi quyền của người hỏi",
}
MODELS = {"user": User, "task": Task, "event": Event, "fund_period": FundPeriod,
          "fund_due": FundDue, "fund_payment": FundPayment, "fund_collection": FundCollection}
FUND_INTENTS = {"partial_payments", "unpaid_members", "fund_overview", "collected_total"}


def save_evidence(actor, message, result):
    """Lưu cùng giao dịch với câu trả lời; không gắn nhãn kiểm chứng cho bản nháp/model text."""
    if not isinstance(result, dict) or result.get("verification") != "database":
        return None
    intent = result.get("intent", "")
    labels = [QUERY_LABELS[intent]] if intent in QUERY_LABELS else []
    if intent == "agent_answer":
        # The orchestrator combines fixed read tools. Do not invent SQL or citations.
        labels = ["Kết hợp kết quả của các công cụ tra cứu được phép; nguồn chi tiết bên dưới"]
    if not labels:
        return None
    sources = {}
    for name, values in (result.get("sources") or {}).items():
        if name not in MODELS or not isinstance(values, list):
            continue
        sources[name] = list(dict.fromkeys(value for value in values
                                         if type(value) is int and value > 0))[:100]
    entry = ChatEvidence(message_id=message.id, user_id=actor.id,
                         role_scope=actor.chuc_vu, intent=intent,
                         permissions=role_permissions(actor.chuc_vu),
                         sources=sources, query_labels=labels,
                         answer_sha256=sha256(message.content.encode()).hexdigest())
    db.session.add(entry)
    return entry


def evidence_for(actor, message_id):
    """Kiểm tra lại chủ sở hữu, quyền hiện tại và dấu nội dung trước khi xem nguồn."""
    require(approved(actor))
    message = db.session.get(ChatMessage, message_id)
    entry = db.session.get(ChatEvidence, message_id)
    if not message or not entry or message.user_id != actor.id or entry.user_id != actor.id:
        raise DomainError("Không tìm thấy nguồn kiểm chứng.", 404)
    require(entry.role_scope == actor.chuc_vu and entry.permissions == role_permissions(actor.chuc_vu),
            "Quyền đã thay đổi sau lần tra cứu; hãy hỏi lại để kiểm chứng với quyền hiện tại.")
    if sha256(message.content.encode()).hexdigest() != entry.answer_sha256:
        raise DomainError("Nội dung lịch sử đã thay đổi; cần thực hiện lại truy vấn.", 409)
    if entry.intent in {"inactive_members"}:
        require(can_manage_members(actor))
    if entry.intent in {"daily_digest", "verified_report", "monthly_report", "agent_answer"}:
        require(is_board(actor))
    if entry.intent == "highest_absence":
        require(can_manage_events(actor))
    items = []
    hidden = 0
    for name, ids in entry.sources.items():
        for item_id in ids:
            row = db.session.get(MODELS[name], item_id)
            item = _source_item(actor, name, row) if row else None
            if item:
                items.append(item)
            else:
                hidden += 1
    return entry, message, items, hidden


def _source_item(actor, name, row):
    """Chỉ sinh liên kết nội bộ; dữ liệu đã mất quyền hoặc đã xóa không được hiện lại."""
    if name == "user":
        if not board_members(actor).filter(User.id == row.id).first():
            return None
        return {"label": f"Thành viên {row.mssv} — {row.ho_ten}",
                "url": url_for("member.member_list")}
    if name == "task":
        if not can_manage_events(actor) and row.assignee_id != actor.id:
            return None
        return {"label": f"Task #{row.id}: {row.ten_task}",
                "url": url_for("event.event_detail", event_id=row.event_id)}
    if name == "event":
        return {"label": f"Sự kiện #{row.id}: {row.ten_su_kien}",
                "url": url_for("event.event_detail", event_id=row.id)}
    if name in {"fund_due", "fund_payment"}:
        if not can_manage_funds(actor) and row.user_id != actor.id:
            return None
        label = "Đóng quỹ theo kỳ" if name == "fund_due" else "Thanh toán khoản thu"
        return {"label": f"{label} #{row.id}", "url": url_for("funds.funds_home")}
    if name == "fund_period":
        if not can_manage_funds(actor) and not FundDue.query.filter_by(period_id=row.id, user_id=actor.id).first():
            return None
        return {"label": f"Kỳ quỹ: {row.ten_ky}", "url": url_for("funds.funds_home")}
    if name == "fund_collection":
        from .funds import target_members
        if not can_manage_funds(actor) and not target_members(row).filter(User.id == actor.id).first():
            return None
        return {"label": f"Khoản thu #{row.id}: {row.ten_khoan_thu}",
                "url": url_for("funds.funds_home")}
    return None
