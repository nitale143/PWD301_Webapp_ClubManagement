"""Durable claims and controlled manual retries for application email."""

import hashlib
import re
import uuid
from datetime import timedelta

from flask import current_app
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app import db
from app.mail_models import EmailDelivery
from app.models import EmailProposal, MemberRecord, User
from app.timeutil import utcnow


MAX_STORED_BODY = 20_000
BODY_RETENTION_DAYS = 90
STALE_SEND_MINUTES = 3
_SENSITIVE_CONTENT = re.compile(
    r"(?i)(?:https?://|www\.|mật\s*khẩu|password|passcode|\botp\b|"
    r"\b(?:confirm|reset|token|password)[=/]|mã\s+(?:xác\s+nhận|otp))|"
    r"\b[A-Za-z0-9_-]{40,}\b"
)
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_EMAIL_ADDRESS = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$"
)


class DeliveryRetryError(ValueError):
    """A delivery cannot be retried under its current persisted state."""


def _session():
    """An isolated session keeps delivery commits separate from caller work."""
    return Session(bind=db.engine, expire_on_commit=False)


def _stored_content(subject, body):
    subject_text = subject if isinstance(subject, str) else ""
    body_text = body if isinstance(body, str) else ""
    protected = bool(_SENSITIVE_CONTENT.search(subject_text + "\n" + body_text))
    bounded = len(body_text) <= MAX_STORED_BODY
    if protected:
        return "[Nội dung nhạy cảm đã ẩn]", None, True, False
    if not bounded:
        return subject_text[:255], None, False, False
    return subject_text[:255], body_text, False, True


def _fingerprint(*values):
    digest = hashlib.sha256()
    for value in values:
        text = value if isinstance(value, str) else f"<{type(value).__name__}>"
        encoded = text.encode("utf-8", "replace")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _is_email_proposal(session, recipient, subject, body):
    """Keep EmailProposal's own review/reconcile gate authoritative for its mail."""
    if not all(isinstance(value, str) for value in (recipient, subject, body)):
        return False
    return session.query(EmailProposal.id).filter_by(
        recipient_email=recipient, subject=subject, body=body, status="sending"
    ).first() is not None


def _is_active_recipient(session, recipient):
    if recipient is None or recipient.status != "approved":
        return False
    record = session.get(MemberRecord, recipient.id)
    return record is None or (record.luu_tru_luc is None and record.tinh_trang != "left")


def _register_delivery(recipient, subject, body, idempotency_key=None):
    if not isinstance(idempotency_key, str) or not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        idempotency_key = uuid.uuid4().hex

    saved_subject, saved_body, sensitive, bounded = _stored_content(subject, body)
    request_fingerprint = _fingerprint(recipient, subject, body)
    with _session() as session:
        try:
            recipient_text = (
                recipient if isinstance(recipient, str) and len(recipient) <= 254
                and _EMAIL_ADDRESS.fullmatch(recipient) and ".." not in recipient
                else "[invalid recipient]"
            )
            sender = current_app.config.get("MAIL_DEFAULT_SENDER", "")
            sender = sender[:254] if isinstance(sender, str) else ""
            recipient_user = None
            if recipient_text != "[invalid recipient]":
                recipient_user = session.query(User.id).filter(
                    func.lower(User.email) == recipient_text.lower()
                ).first()
                recipient_user = session.get(User, recipient_user[0]) if recipient_user else None
            proposal_mail = _is_email_proposal(session, recipient, subject, body)
            retry_allowed = bool(
                bounded and saved_body is not None and not sensitive and not proposal_mail
                and (recipient_user is None or _is_active_recipient(session, recipient_user))
            )
            row = EmailDelivery(
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                message_id=f"<{uuid.uuid4().hex}@club-manager.local>",
                recipient_user_id=recipient_user.id if recipient_user else None,
                recipient_email=recipient_text[:254],
                sender_email=sender,
                last_sender_email=sender,
                subject=saved_subject,
                body=saved_body if retry_allowed else None,
                retry_allowed=retry_allowed,
                sensitive_content=sensitive,
                status="pending",
                body_expires_at=(utcnow() + timedelta(days=BODY_RETENTION_DAYS)
                                 if retry_allowed else None),
            )
            session.add(row)
            session.commit()
            return row.id
        except IntegrityError:
            session.rollback()
            existing = session.query(EmailDelivery).filter_by(
                idempotency_key=idempotency_key
            ).first()
            if existing is None or existing.request_fingerprint != request_fingerprint:
                current_app.logger.warning("Email delivery record could not be persisted")
                return None
            return existing.id
        except SQLAlchemyError:
            session.rollback()
            current_app.logger.warning("Email delivery record could not be persisted")
            return None


def _status_for_existing(delivery_id):
    with _session() as session:
        row = session.get(EmailDelivery, delivery_id)
        return row.status if row else None


def _claim(delivery_id, allowed_statuses, actor_id=None, confirm_unknown=False):
    now = utcnow()
    with _session() as session:
        row = session.get(EmailDelivery, delivery_id)
        if row is None or row.status not in allowed_statuses:
            return False
        observed_attempt_count = row.attempt_count
        if row.status == "unknown" and not confirm_unknown:
            raise DeliveryRetryError(
                "Kết quả gửi trước chưa rõ. Hãy kiểm tra thư đã gửi và xác nhận trước khi thử lại."
            )
        if row.status != "pending":
            if not row.retry_allowed or row.body is None:
                raise DeliveryRetryError("Thư này không có nội dung an toàn để thử gửi lại.")
            if row.recipient_user_id:
                recipient = session.get(User, row.recipient_user_id)
                if (recipient is None or recipient.email.strip() != row.recipient_email
                        or not _is_active_recipient(session, recipient)):
                    raise DeliveryRetryError(
                        "Tài khoản người nhận đã thay đổi hoặc không còn hoạt động; thư cũ không thể được gửi lại."
                    )
        values = {
            "status": "sending",
            "attempt_count": observed_attempt_count + 1,
            "last_attempt_at": now,
            "last_attempt_by_id": actor_id,
            "last_sender_email": (current_app.config.get("MAIL_DEFAULT_SENDER") or "")[:254],
            "updated_at": now,
            "last_error_code": None,
            "last_failure_phase": None,
        }
        if row.status == "unknown" and confirm_unknown:
            values.update(unknown_confirmed_by_id=actor_id, unknown_confirmed_at=now)
        claimed = session.execute(
            update(EmailDelivery)
            .where(EmailDelivery.id == delivery_id,
                   EmailDelivery.status == row.status,
                   EmailDelivery.retry_allowed == row.retry_allowed,
                   # Status can ABA (unknown -> sending -> unknown) while this
                   # caller waits. The count identifies the exact row version
                   # whose retry confirmation was checked above.
                   EmailDelivery.attempt_count == observed_attempt_count)
            .values(**values)
        )
        session.commit()
        return observed_attempt_count + 1 if claimed.rowcount == 1 else None


def _perform_attempt(delivery_id, attempt_number, initial_content=None):
    from app.mail import _send_delivery_message

    with _session() as session:
        row = session.get(EmailDelivery, delivery_id)
        if (row is None or row.status != "sending"
                or row.attempt_count != attempt_number):
            return False
        if initial_content is None:
            recipient, subject, body = row.recipient_email, row.subject, row.body
        else:
            recipient, subject, body = initial_content
        message_id = row.message_id

    try:
        outcome, error_code, phase = _send_delivery_message(
            recipient, subject, body, message_id
        )
    except Exception as exc:
        # An unexpected exception during the send path is conservatively ambiguous.
        current_app.logger.warning("Email delivery attempt failed (%s)", type(exc).__name__)
        outcome, error_code, phase = "unknown", type(exc).__name__, "send"

    now = utcnow()
    with _session() as session:
        values = {
            "status": outcome,
            "last_error_code": (error_code or "")[:80] or None,
            "last_failure_phase": phase,
            "updated_at": now,
        }
        if outcome == "sent":
            values["sent_at"] = now
        elif outcome == "dev-saved":
            values["dev_saved_at"] = now
        if outcome not in {"failed", "unknown"}:
            values.update(body=None, retry_allowed=False, body_expires_at=None)
        try:
            saved = session.execute(
                update(EmailDelivery)
                .where(EmailDelivery.id == delivery_id,
                       EmailDelivery.status == "sending",
                       EmailDelivery.attempt_count == attempt_number)
                .values(**values)
            )
            if saved.rowcount != 1:
                session.rollback()
                return False
            session.commit()
        except SQLAlchemyError:
            session.rollback()
            current_app.logger.warning("Email delivery outcome could not be persisted")
            # The durable row remains 'sending'; the dashboard will mark it unknown later.
            return False
    return outcome in {"sent", "dev-saved"}


def send_tracked_email(recipient, subject, body, idempotency_key=None):
    purge_expired_delivery_bodies()
    delivery_id = _register_delivery(recipient, subject, body, idempotency_key)
    if delivery_id is None:
        return False
    attempt_number = _claim(delivery_id, {"pending"})
    if not attempt_number:
        return _status_for_existing(delivery_id) in {"sent", "dev-saved"}
    return _perform_attempt(
        delivery_id, attempt_number, initial_content=(recipient, subject, body)
    )


def retry_email_delivery(delivery_id, actor_id, confirm_unknown=False):
    purge_expired_delivery_bodies()
    current = _status_for_existing(delivery_id)
    if current not in {"failed", "unknown"}:
        raise DeliveryRetryError("Chỉ email gửi lỗi hoặc chưa rõ kết quả mới có thể thử lại.")
    claimed = _claim(delivery_id, {current}, actor_id=actor_id,
                     confirm_unknown=confirm_unknown)
    if not claimed:
        raise DeliveryRetryError("Email này vừa được một người khác xử lý.")
    return _perform_attempt(delivery_id, claimed)


def purge_expired_delivery_bodies():
    """Expire private retry payloads after their bounded retention window."""
    now = utcnow()
    with _session() as session:
        try:
            changed = session.execute(
                update(EmailDelivery)
                .where(EmailDelivery.body.is_not(None), EmailDelivery.body_expires_at <= now)
                .values(body=None, retry_allowed=False, body_expires_at=None, updated_at=now)
            ).rowcount
            session.commit()
            return changed
        except SQLAlchemyError:
            session.rollback()
            current_app.logger.warning("Expired email delivery content could not be removed")
            return 0


def mark_stale_deliveries_unknown():
    """A process crash after claiming a send must never cause an automatic replay."""
    cutoff = utcnow() - timedelta(minutes=STALE_SEND_MINUTES)
    with _session() as session:
        try:
            changed = session.execute(
                update(EmailDelivery)
                .where(EmailDelivery.status == "sending",
                       EmailDelivery.last_attempt_at <= cutoff)
                .values(status="unknown", last_error_code="process_interrupted",
                        last_failure_phase="send", updated_at=utcnow())
            ).rowcount
            session.commit()
            return changed
        except SQLAlchemyError:
            session.rollback()
            current_app.logger.warning("Stale email delivery status could not be updated")
            return 0
