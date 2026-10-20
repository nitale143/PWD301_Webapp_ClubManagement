"""Durable delivery metadata for synchronous email sends."""

from app import db
from app.timeutil import utcnow


class EmailDelivery(db.Model):
    """One authorized email send request and its SMTP outcome.

    Message bodies are retained only when they are bounded and do not contain a
    URL or token-like value. The dashboard never renders the body.
    """

    __tablename__ = "email_delivery"

    id = db.Column(db.Integer, primary_key=True)
    idempotency_key = db.Column(db.String(64), nullable=False, unique=True)
    request_fingerprint = db.Column(db.String(64), nullable=False)
    message_id = db.Column(db.String(255), nullable=False, unique=True)
    recipient_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    recipient_email = db.Column(db.String(254), nullable=False, default="")
    sender_email = db.Column(db.String(254), nullable=False, default="")
    last_sender_email = db.Column(db.String(254), nullable=False, default="")
    subject = db.Column(db.String(255), nullable=False, default="")
    body = db.Column(db.Text, nullable=True)
    retry_allowed = db.Column(db.Boolean, nullable=False, default=False)
    sensitive_content = db.Column(db.Boolean, nullable=False, default=False)
    status = db.Column(db.String(16), nullable=False, default="pending", index=True)
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    last_error_code = db.Column(db.String(80), nullable=True)
    last_failure_phase = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    body_expires_at = db.Column(db.DateTime, nullable=True)
    last_attempt_at = db.Column(db.DateTime, nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)
    dev_saved_at = db.Column(db.DateTime, nullable=True)
    last_attempt_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    unknown_confirmed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    unknown_confirmed_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.CheckConstraint(
            "status IN ('pending','sending','sent','failed','unknown','skipped','dev-saved')",
            name="ck_email_delivery_status",
        ),
        db.CheckConstraint("attempt_count >= 0", name="ck_email_delivery_attempt_count"),
        db.Index("ix_email_delivery_status_created", "status", "created_at"),
    )
