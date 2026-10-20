"""Bằng chứng tra cứu gắn với từng câu trả lời, không phải suy nghĩ riêng của model."""
from app import db
from app.timeutil import utcnow


class ChatEvidence(db.Model):
    """Lưu nguồn và dấu kiểm tra của câu trả lời từ truy vấn được định nghĩa trước."""
    __tablename__ = "chat_evidence"
    message_id = db.Column(db.Integer, db.ForeignKey("chat_message.id"), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    role_scope = db.Column(db.String(20), nullable=False)
    permissions = db.Column(db.Integer, nullable=False)
    intent = db.Column(db.String(80), nullable=False)
    sources = db.Column(db.JSON, nullable=False, default=dict)
    query_labels = db.Column(db.JSON, nullable=False, default=list)
    answer_sha256 = db.Column(db.String(64), nullable=False)
    captured_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    query_version = db.Column(db.String(24), nullable=False, default="fia-verified-v1")
