"""One policy path for the browser chat and JSON assistant API."""

from datetime import timedelta

from flask import current_app

from app.models import ChatMessage
from .assistant import get_provider
from .briefings import maybe_answer_briefing
from .common import DomainError
from .email_proposals import maybe_create_email_draft
from .event_drafts import maybe_handle_draft
from .verified_queries import maybe_answer_member_query
from app.timeutil import club_now


def check_chat_rate(actor):
    """Giới hạn số câu hỏi AI mỗi phút cho một người (AI_CHAT_MESSAGES_PER_MINUTE)."""
    count = ChatMessage.query.filter(
        ChatMessage.user_id == actor.id, ChatMessage.role == "user",
        ChatMessage.thoi_gian >= club_now() - timedelta(minutes=1),
    ).count()
    if count >= current_app.config["AI_CHAT_MESSAGES_PER_MINUTE"]:
        raise DomainError("Bạn gửi câu hỏi quá nhanh. Vui lòng đợi một phút.", 429)


def respond(actor, message, conversation=None):
    """Trả lời một câu hỏi chat: thử lần lượt nháp email → báo cáo → bản nháp sự kiện → đọc dữ liệu
    đã kiểm chứng → nhà cung cấp AI.
    """
    history = None
    if conversation is not None:
        from .chat_conversations import owned_conversation
        owned_conversation(actor, conversation.id, active=True)
        history = list(reversed(ChatMessage.query.filter_by(
            user_id=actor.id, conversation_id=conversation.id)
            .order_by(ChatMessage.id.desc()).limit(6).all()))
    return (maybe_create_email_draft(actor, message)
            or maybe_answer_briefing(actor, message)
            or maybe_handle_draft(actor, message, conversation)
            or maybe_answer_member_query(actor, message)
            or get_provider().answer(actor, message, history=history))


def respond_events(actor, message, conversation=None, message_id=None):
    """Stream safe status and terminal result events for the browser/API agent."""
    from .agent_orchestrator import respond_events as orchestrate
    yield from orchestrate(actor, message, conversation=conversation,
                           message_id=message_id)
