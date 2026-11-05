"""Owned, persistent conversations with bounded keyset message pages."""


from flask import session
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import ChatConversation, ChatMessage
from .common import DomainError
from app.timeutil import utcnow

DEFAULT_TITLE = "Cuộc trò chuyện mới"
MESSAGE_PAGE_SIZE = 40
CONVERSATION_PAGE_SIZE = 20


def positive_id(value):
    """Ép kiểu id/trang thành số nguyên dương hợp lệ, sai → DomainError."""
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit():
        raise DomainError("Mã cuộc trò chuyện hoặc trang không hợp lệ.")
    number = int(value)
    if not 0 < number <= 2147483647:
        raise DomainError("Mã cuộc trò chuyện hoặc trang không hợp lệ.")
    return number


def owned_conversation(actor, conversation_id, active=False):
    """Lấy cuộc trò chuyện thuộc đúng người dùng (không thì 404); `active=True` thì chặn cuộc đã
    lưu trữ.
    """
    conversation = ChatConversation.query.filter_by(
        id=positive_id(conversation_id), user_id=actor.id).populate_existing().first()
    if conversation is None:
        raise DomainError("Không tìm thấy cuộc trò chuyện.", 404)
    if active and conversation.archived:
        raise DomainError("Hãy khôi phục cuộc trò chuyện trước khi gửi tin nhắn.", 409)
    return conversation


def checked_title(title):
    """Kiểm tra tên cuộc trò chuyện 1–100 ký tự."""
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 100:
        raise DomainError("Tên cuộc trò chuyện phải có từ 1 đến 100 ký tự.")
    return title.strip()


def create_conversation(actor, title=None):
    """Tạo cuộc trò chuyện mới và ghi nhớ là cuộc đang chọn trong session."""
    conversation = ChatConversation(user_id=actor.id,
        title=DEFAULT_TITLE if title is None or title == "" else checked_title(title))
    db.session.add(conversation)
    db.session.flush()
    session[f"ai_conversation_{actor.id}"] = conversation.id
    return conversation


def _import_unscoped_history(actor):
    """Compatibility for legacy callers inserting messages after startup migration."""
    query = ChatMessage.query.filter_by(user_id=actor.id, conversation_id=None)
    if not query.first():
        return
    legacy = ChatConversation.query.filter_by(user_id=actor.id, legacy_marker="legacy").first()
    if legacy is None:
        try:
            with db.session.begin_nested():
                legacy = ChatConversation(user_id=actor.id, legacy_marker="legacy",
                                          title="Lịch sử trước nâng cấp")
                db.session.add(legacy)
                db.session.flush()
        except IntegrityError:
            legacy = ChatConversation.query.filter_by(
                user_id=actor.id, legacy_marker="legacy").one()
    query.update({ChatMessage.conversation_id: legacy.id}, synchronize_session=False)
    db.session.commit()


def select_conversation(actor, requested_id=None, create=False, active=False):
    """Chọn cuộc trò chuyện theo id yêu cầu hoặc cuộc đang mở trong session (tạo mới nếu `create`)."""
    _import_unscoped_history(actor)
    key = f"ai_conversation_{actor.id}"
    if requested_id is not None:
        conversation = owned_conversation(actor, requested_id, active=active)
    else:
        selected = session.get(key)
        conversation = (ChatConversation.query.filter_by(id=selected, user_id=actor.id,
                        archived=False).first() if selected else None)
        if conversation is None:
            conversation = ChatConversation.query.filter_by(user_id=actor.id, archived=False)\
                .order_by(ChatConversation.updated_at.desc(), ChatConversation.id.desc()).first()
        if conversation is None and create:
            conversation = create_conversation(actor)
    if conversation:
        if active and conversation.archived:
            raise DomainError("Hãy khôi phục cuộc trò chuyện trước khi gửi tin nhắn.", 409)
        session[key] = conversation.id
    return conversation


def append_exchange(actor, conversation, question, reply):
    """Lưu cặp câu hỏi – trả lời vào cuộc trò chuyện (đặt tiêu đề từ câu hỏi đầu tiên)."""
    owned_conversation(actor, conversation.id, active=True)
    if conversation.title == DEFAULT_TITLE and not ChatMessage.query.filter_by(
            conversation_id=conversation.id, user_id=actor.id, role="user").first():
        conversation.title = " ".join(question.split())[:100] or DEFAULT_TITLE
    for role, content in (("user", question), ("ai", reply)):
        db.session.add(ChatMessage(user_id=actor.id, conversation_id=conversation.id,
                                   role=role, content=content))
    conversation.updated_at = utcnow()


def begin_turn(actor, conversation, question):
    """Persist the human question before a potentially slow model call."""
    owned_conversation(actor, conversation.id, active=True)
    if conversation.title == DEFAULT_TITLE and not ChatMessage.query.filter_by(
            conversation_id=conversation.id, user_id=actor.id, role="user").first():
        conversation.title = " ".join(question.split())[:100] or DEFAULT_TITLE
    message = ChatMessage(user_id=actor.id, conversation_id=conversation.id,
                          role="user", content=question)
    db.session.add(message)
    conversation.updated_at = utcnow()
    db.session.flush()
    db.session.commit()
    return message


def finish_turn(actor, conversation, reply, user_message_id, run_id, permission_stamp=None):
    """Save a grounded reply and safe operational audit before streaming it."""
    from .access import require_current_approval, require_unchanged_scope
    from .common import audit

    require_current_approval(actor)
    if permission_stamp is not None:
        require_unchanged_scope(actor, permission_stamp)
    owned_conversation(actor, conversation.id, active=True)
    message = ChatMessage(user_id=actor.id, conversation_id=conversation.id,
                          role="ai", content=reply["answer"])
    db.session.add(message)
    conversation.updated_at = utcnow()
    audit(actor, "assistant_query", actor, {
        "intent": reply.get("intent", "unsupported"),
        "provider": reply.get("provider", "rules"),
        "agent_mode": reply.get("agent_mode", "rules_fallback"),
        "steps": reply.get("steps", []),
        "run_id": run_id, "user_message_id": user_message_id,
    })
    db.session.flush()
    db.session.commit()
    return message


def conversation_list(actor, page=1, archived=False):
    """Danh sách cuộc trò chuyện theo trang (20 mục/trang), mới cập nhật trước."""
    page = positive_id(page)
    rows = ChatConversation.query.filter_by(user_id=actor.id, archived=archived)\
        .order_by(ChatConversation.updated_at.desc(), ChatConversation.id.desc())\
        .offset((page - 1) * CONVERSATION_PAGE_SIZE).limit(CONVERSATION_PAGE_SIZE + 1).all()
    return rows[:CONVERSATION_PAGE_SIZE], len(rows) > CONVERSATION_PAGE_SIZE, page


def message_page(actor, conversation, before=None, after=None):
    """Tin nhắn của cuộc trò chuyện theo trang dạng keyset (before/after theo id)."""
    if before is not None and after is not None:
        raise DomainError("Chỉ chọn một hướng tải lịch sử.")
    if conversation is None:
        if before is not None or after is not None:
            raise DomainError("Không tìm thấy cuộc trò chuyện.", 404)
        return [], None, None
    owned_conversation(actor, conversation.id)
    query = ChatMessage.query.filter_by(user_id=actor.id, conversation_id=conversation.id)
    cursor = before if before is not None else after
    if cursor is not None:
        cursor = positive_id(cursor)
        if not query.filter_by(id=cursor).first():
            raise DomainError("Không tìm thấy tin nhắn trong cuộc trò chuyện.", 404)
    if after is not None:
        rows = query.filter(ChatMessage.id > cursor).order_by(ChatMessage.id.asc())\
            .limit(MESSAGE_PAGE_SIZE).all()
    else:
        subset = query.filter(ChatMessage.id < cursor) if before is not None else query
        rows = list(reversed(subset.order_by(ChatMessage.id.desc()).limit(MESSAGE_PAGE_SIZE).all()))
    older = rows[0].id if rows and query.filter(ChatMessage.id < rows[0].id).first() else None
    newer = rows[-1].id if rows and query.filter(ChatMessage.id > rows[-1].id).first() else None
    return rows, older, newer


def conversation_json(conversation):
    """Chuyển cuộc trò chuyện thành JSON."""
    return {"id": conversation.id, "title": conversation.title,
            "archived": conversation.archived,
            "created_at": conversation.created_at.isoformat(),
            "updated_at": conversation.updated_at.isoformat()}
