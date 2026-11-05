"""Conservative natural-language intake for reviewable event/task proposals.

Entity IDs and dates come from the database or explicit user text, never from
an unconstrained model answer. Ambiguity produces a question, not a write.
"""

import re
from datetime import timedelta

from app import db
from app.models import AIProposal, AssistantPending, ConversationPending, Ban, Event
from .access import can_manage_events, require, scoped_members
from app.models import Permission
from .assistant import RulesProvider, _normalize
from .common import DomainError
from .proposals import create_proposal
from .natural_datetime import parse_natural_datetimes
from app.timeutil import utcnow


def _kind(message):
    """Nhận diện câu chat là yêu cầu tạo sự kiện (event) hay giao task (task)."""
    text = _normalize(message).strip()
    text = re.sub(r"^(?:ai oi[,!]*\s*|hay\s+|giup\s+|toi muon\s+)+", "", text)
    if re.search(r"\b(?:chia|giao|phan cong|tao|de xuat)\b", text) and re.search(
            r"\b(?:task|cong viec|viec)\b", text):
        return "task"
    if re.search(r"\b(?:tao|de xuat|lap|to chuc)\b", text) and re.search(
            r"\b(?:su kien|workshop|hoi thao)\b", text):
        return "event"
    return None


def _pending(actor, conversation=None):
    """Đề xuất đang chờ bổ sung thông tin của người dùng (theo cuộc trò chuyện)."""
    if conversation is not None:
        from .chat_conversations import owned_conversation
        owned_conversation(actor, conversation.id, active=True)
        return db.session.get(ConversationPending, conversation.id)
    return db.session.get(AssistantPending, actor.id)


def _new_pending(actor, kind, text, conversation=None):
    """Tạo bản ghi đề xuất đang chờ bổ sung."""
    if conversation is not None:
        return ConversationPending(user_id=actor.id, conversation_id=conversation.id,
                                   kind=kind, text=text)
    return AssistantPending(user_id=actor.id, kind=kind, text=text)


def is_natural_proposal_command(message, actor=None, conversation=None):
    """True nếu câu chat là yêu cầu tạo đề xuất hoặc đang có đề xuất chờ bổ sung."""
    if _kind(message):
        return True
    return bool(actor and _pending(actor, conversation))


def is_pending_deadline_reply(actor, message, conversation=None):
    """Recognize an explicit deadline continuation, not a task lookup."""
    pending = _pending(actor, conversation)
    if (not pending or pending.kind != "task"
            or pending.updated_at < utcnow() - timedelta(minutes=30)):
        return False
    return bool(re.match(
        r"^(?:han(?:\s+cua)?(?:\s+(?:task|cong viec))?(?:\s+cua toi)?|deadline)"
        r"(?:\s+la\b|\s*[:=]|\s+(?:toi|luc|ngay|hom nay|ngay mai|\d))",
        _normalize(message).strip(),
    ))


def _entity(rows, name_attr, message, label):
    """Tìm đúng một bản ghi có tên xuất hiện trong câu (ưu tiên tên dài nhất)."""
    text = _normalize(message)
    matches = [row for row in rows if len(_normalize(getattr(row, name_attr))) >= 3
               and re.search(r"(?<!\w)" + re.escape(_normalize(getattr(row, name_attr))) + r"(?!\w)", text)]
    if len(matches) > 1:
        longest = max(len(_normalize(getattr(row, name_attr))) for row in matches)
        long_matches = [row for row in matches if len(_normalize(getattr(row, name_attr))) == longest]
        if len(long_matches) == 1 and all(
            _normalize(getattr(row, name_attr)) in _normalize(getattr(long_matches[0], name_attr))
            for row in matches):
            matches = long_matches
    if len(matches) > 1:
        raise DomainError(f"Có nhiều {label} phù hợp; hãy nêu mã để chọn chính xác.")
    return matches[0] if matches else None


def _event(message):
    """Sự kiện đang mở được nhắc trong câu (theo mã hoặc tên)."""
    events = Event.query.filter(Event.trang_thai.notin_(["da_ket_thuc", "cancelled"])).all()
    by_code = [row for row in events if re.search(
        r"(?<!\w)" + re.escape(row.ma_su_kien) + r"(?!\w)", message, re.I)]
    if len(by_code) > 1:
        raise DomainError("Có nhiều sự kiện được nhắc đến; hãy chọn một sự kiện.")
    return by_code[0] if by_code else _entity(events, "ten_su_kien", message, "sự kiện")


def _ban(message):
    """Ban được nhắc trong câu (theo số hoặc tên); nhiều ban thì yêu cầu chọn."""
    text = _normalize(message)
    match = re.search(r"\bban\s*#?\s*(\d+)\b", text)
    if match:
        ban = db.session.get(Ban, int(match.group(1)))
        if ban is None:
            raise DomainError(f"Không có ban #{match.group(1)} trong hệ thống. Hãy kiểm tra lại mã ban.")
        return ban
    named = _entity(Ban.query.all(), "ten_ban", message, "ban")
    if re.search(r"\b(?:ca\s+)?\d+\s+ban\b|\btat ca (?:cac )?ban\b", text) and not named:
        raise DomainError("Một task chỉ thuộc một ban. Hãy nói rõ task nào cho ban nào; "
                          "nếu cần 3 ban, tạo ba đề xuất để người quản lý sự kiện kiểm tra từng ban.")
    return named


def _assignee(actor, message):
    """Người phụ trách được nhắc trong câu (MSSV hoặc họ tên), trong phạm vi quản lý sự kiện."""
    members = scoped_members(actor, Permission.MANAGE_EVENTS).all()
    codes = [row for row in members if re.search(
        r"(?<!\w)" + re.escape(row.mssv) + r"(?!\w)", message, re.I)]
    if len(codes) > 1:
        raise DomainError("Có nhiều người được nhắc đến; hãy dùng một MSSV.")
    if codes:
        return codes[0]
    normalized = _normalize(message)
    matches = [row for row in members if len(_normalize(row.ho_ten)) >= 4 and re.search(
        r"(?<!\w)" + re.escape(_normalize(row.ho_ten)) + r"(?!\w)", normalized)]
    exact_accented = [row for row in matches if row.ho_ten.casefold() in message.casefold()
                      and _normalize(row.ho_ten) != row.ho_ten.casefold()]
    matches = exact_accented or matches
    if len(matches) > 1:
        raise DomainError("Có nhiều thành viên trùng hoặc gần giống tên; hãy dùng MSSV.")
    if matches:
        return matches[0]
    # An explicitly named recipient must never silently become an auto-assignment.
    requested = re.search(r"\bcho\s+(.+?)(?=\b(?:cho|trong|thuoc|han|vao|ngay|luc|den|tu)\b|$)",
                          normalized)
    if requested and not re.match(r"(?:ban|su kien|clb|cac ban|tat ca)\b", requested.group(1).strip()):
        raise DomainError("Không tìm thấy người được chỉ định trong phạm vi bạn quản lý. "
                          "Hãy nêu đúng họ tên hoặc MSSV; hệ thống sẽ không tự đổi người nhận.")
    return None


def _dates(message):
    """Các mốc thời gian đầy đủ trong câu (dùng natural_datetime)."""
    return parse_natural_datetimes(message)


def _brief(message, kind, event=None, ban=None, member=None, date_parts=()):
    """Phần mô tả còn lại của câu sau khi bỏ các cụm đã nhận diện (sự kiện, ban, người, thời gian)."""
    text = message
    # Remove the relationship connector, not the word 'thuộc' in a task title.
    text = re.sub(r"(?i)\b(?:thuộc|thuoc)\s+ban\b", " ban ", text)
    text = re.sub(r"(?i)\b(?:cả\s+)?\d+\s+ban\b", " ", text)
    text = re.sub(r"(?i)\bban\s*#?\s*\d+\b", " ", text)
    for raw in date_parts:
        text = re.sub(re.escape(raw), " ", text, flags=re.I)
    for name in (getattr(event, "ten_su_kien", None), getattr(event, "ma_su_kien", None),
                 getattr(ban, "ten_ban", None), getattr(member, "ho_ten", None),
                 getattr(member, "mssv", None)):
        if name:
            text = re.sub(re.escape(name), " ", text, flags=re.I)
    text = re.sub(r"(?i)^\s*(?:AI ơi[,!]?)?\s*(?:hãy\s+|giúp\s+|tôi muốn\s+)*"
                  r"(?:đề xuất\s+|tạo\s+|lập\s+|tổ chức\s+|chia\s+|giao\s+|phân công\s+)*"
                  r"(?:sự kiện|workshop|hội thảo|task|công việc|việc)\b", " ", text)
    text = re.sub(r"(?i)\b(?:cho|trong|sự kiện|ban|hạn|ngày|lúc|đến|từ|vào|là|tên|bắt đầu|kết thúc|và)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip(" ,.;:-")[:2000]


def maybe_create_natural_proposal(actor, message, conversation=None):
    """Xử lý yêu cầu tạo sự kiện/task bằng ngôn ngữ tự nhiên: hỏi lại thông tin thiếu, đủ thì tạo
    AIProposal chờ duyệt.
    """
    pending = _pending(actor, conversation)
    if pending and pending.updated_at < utcnow() - timedelta(minutes=30):
        db.session.delete(pending)
        pending = None
    if pending and _normalize(message).strip() in {"huy", "bo qua", "khong tao nua"}:
        db.session.delete(pending)
        return {"intent": "proposal_cancelled", "provider": "database", "preview": True,
                "answer": "Đã hủy yêu cầu đang bổ sung; chưa tạo sự kiện hay task."}
    current_kind = _kind(message)
    if not current_kind and not pending:
        return None
    require(can_manage_events(actor), "Chỉ người có quyền quản lý sự kiện được tạo đề xuất sự kiện/task.")
    kind = current_kind or pending.kind
    # A full new request replaces the prior incomplete request. Short answers
    # still add missing details to the pending conversation.
    continuation = message
    separator = " "
    if pending and not current_kind and is_pending_deadline_reply(actor, message, conversation):
        # Deadline labels are form metadata, not part of the task title.
        continuation = re.sub(
            r"(?i)^\s*(?:(?:hạn|han)(?:\s+(?:của|cua))?"
            r"(?:\s+(?:task|công việc|cong viec))?(?:\s+(?:của tôi|cua toi))?|deadline)"
            r"\s*(?:(?:là|la)\b|[:=])?\s*", "", message, count=1,
        )
        # Keep separate replies separate: a new clock must not pair with a
        # previous date when the user is correcting/conflicting with a deadline.
        separator = " ; "
    text = pending.text + separator + continuation if pending and not current_kind else message
    if len(text) > 4000:
        raise DomainError("Yêu cầu đang quá dài. Gõ 'hủy' rồi viết lại ngắn hơn.")
    try:
        dates = _dates(text)
        if kind == "task" and len({when for when, _raw in dates}) > 1:
            raise DomainError("Yêu cầu nêu nhiều ngày/giờ cho một task; hãy xác nhận hạn cần dùng.")
        ban = _ban(text)
        member = _assignee(actor, text) if kind == "task" else None
        event = _event(text) if kind == "task" else None
    except DomainError as exc:
        if pending is None:
            pending = _new_pending(actor, kind, text, conversation)
            db.session.add(pending)
        else:
            pending.kind, pending.text = kind, text
        return {"intent": "proposal_needs_details", "preview": True, "provider": "database",
                "answer": f"Chưa tạo đề xuất. {exc} Gõ 'hủy' nếu muốn bỏ yêu cầu này."}
    brief = _brief(text, kind, event, ban, member, [raw for _when, raw in dates])
    if pending and not current_kind and kind == "task":
        date_parts = [raw for _when, raw in dates]
        latest = _brief(message, kind, event, ban, member, date_parts)
        previous = _brief(pending.text, kind, event, ban, member, date_parts)
        # A follow-up can restate the same title (including text saved by the
        # old parser). Collapse only exact repeated titles, never new details.
        latest_key = _normalize(latest)
        if latest_key and re.fullmatch(
                rf"(?:{re.escape(latest_key)}(?:[\s,.;:-]+|$))+", _normalize(previous)):
            brief = latest
    missing = []
    if kind == "event":
        if not brief:
            missing.append("tên/nội dung sự kiện")
        if len(dates) < 1:
            missing.append("giờ bắt đầu")
        if len(dates) < 2:
            missing.append("giờ kết thúc")
    else:
        if not event:
            missing.append("sự kiện hiện có (tên hoặc mã)")
        if not ban:
            missing.append("ban thực hiện")
        if not dates:
            missing.append("hạn task (ngày và giờ)")
        if not brief:
            missing.append("nội dung task")
    if missing:
        if pending is None:
            db.session.add(_new_pending(actor, kind, text, conversation))
        else:
            pending.kind, pending.text = kind, text
        return {"intent": "proposal_needs_details", "preview": True, "provider": "database",
                "answer": "Chưa tạo đề xuất. Bạn cho mình thêm: " + ", ".join(missing)
                + ". Có thể trả lời ngay tin tiếp theo; gõ 'hủy' để bỏ."}
    if kind == "event":
        data = {"kind": "event", "brief": brief, "start_at": dates[0][0].isoformat(),
                "end_at": dates[1][0].isoformat(), "ban_id": ban.id if ban else ""}
    else:
        data = {"kind": "task", "brief": brief, "event_id": event.id,
                "ban_id": ban.id, "deadline": dates[-1][0].isoformat(),
                "assignee_id": member.id if member else ""}
    if kind == "event":
        resolved = (f"Thời gian sự kiện: {dates[0][0]:%H:%M ngày %d/%m/%Y} đến "
                    f"{dates[1][0]:%H:%M ngày %d/%m/%Y} (giờ CLB). ")
    else:
        resolved = f"Hạn task: {dates[-1][0]:%H:%M ngày %d/%m/%Y} (giờ CLB). "
    existing = (AIProposal.query.filter_by(created_by_id=actor.id, kind=kind,
                                            request_text=brief, status="pending")
                .filter(AIProposal.created_at >= utcnow() - timedelta(minutes=30))
                .order_by(AIProposal.id.desc()).all())
    for row in existing:
        payload = row.payload
        same = (payload.get("start_at") == data["start_at"]
                and payload.get("end_at") == data["end_at"]
                and payload.get("ban_id") == (data["ban_id"] or None)) if kind == "event" else (
                    payload.get("event_id") == data["event_id"]
                    and payload.get("ban_id") == data["ban_id"]
                    and payload.get("deadline") == data["deadline"]
                    and (not data["assignee_id"] or all(
                        item["assignee_id"] == data["assignee_id"] for item in payload.get("tasks", []))))
        if same:
            if pending is not None:
                db.session.delete(pending)
            return {"intent": "ai_proposal", "preview": True, "provider": "database",
                    "proposal_id": row.id,
                    "answer": f"Đề xuất #{row.id} đã tồn tại và đang chờ duyệt; chưa tạo thêm bản trùng. "
                    + resolved}
    proposal = create_proposal(actor, data, provider=RulesProvider())
    if pending is not None:
        db.session.delete(pending)
    return {"intent": "ai_proposal", "preview": True, "provider": "database",
            "proposal_id": proposal.id,
            "answer": f"Đã tạo đề xuất #{proposal.id} từ câu tự nhiên; chưa áp dụng. " + resolved
            + "Người quản lý sự kiện hãy xem đúng sự kiện, ban, người nhận và thời hạn rồi bấm Duyệt và áp dụng."}
