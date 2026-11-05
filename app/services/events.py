import secrets
from datetime import timedelta

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app import db
from app.models import (
    Attendance, Event, EventDetail, EventFeedback, EventRegistration,
    EventTargetBan, QRCheckinLog,
)
from .access import approved, can_manage_events, require
from .activities import refresh_for_event
from .event_status import ENDED, OPEN, is_terminal
from .common import DomainError, audit, club_now


QR_TTL_SECONDS = 15 * 60
REGISTRATION_STATES = {"registered", "waitlist", "cancelled"}
ATTENDANCE_STATES = {"on_time", "late", "excused", "absent", "pending"}


def _serializer():
    """Bộ ký token QR điểm danh (itsdangerous, salt riêng)."""
    return URLSafeTimedSerializer(current_app.secret_key, salt="event-checkin")


def event_is_terminal(event):
    """Sự kiện đã kết thúc hoặc bị hủy (không cho sửa task/đăng ký)."""
    return is_terminal(event)


def registration_open(event, detail, now=None):
    """Sự kiện còn mở đăng ký: chưa kết thúc/hủy và chưa quá hạn đăng ký.

    Thời gian sự kiện gồm cả setup và báo cáo, nên vẫn đăng ký được sau giờ bắt đầu.
    Không đặt hạn đăng ký thì mở đến khi sự kiện kết thúc.
    """
    now = now or club_now()
    deadline = detail.han_dang_ky if detail and detail.han_dang_ky else event.thoi_gian_ket_thuc
    return (not event_is_terminal(event)
            and event.trang_thai in OPEN
            and now <= deadline)


def eligible(event, member):
    """Thành viên thuộc đối tượng được đăng ký (mọi người, hoặc thuộc ban áp dụng)."""
    detail = db.session.get(EventDetail, event.id)
    if not detail or detail.tat_ca_thanh_vien:
        return True
    target_ids = {row.ban_id for row in EventTargetBan.query.filter_by(event_id=event.id)}
    return bool(target_ids.intersection(link.ban_id for link in member.ban_links))


def register(actor, event, member=None):
    """Đăng ký tham gia (cho mình, hoặc người quản lý sự kiện đăng ký hộ). Sự kiện không giới hạn
    số người nên luôn vào trạng thái registered.
    """
    member = member or actor
    require(approved(actor) and (actor.id == member.id or can_manage_events(actor)))
    if not approved(member):
        raise DomainError("Thành viên chưa được duyệt.")
    detail = db.session.get(EventDetail, event.id)
    if not registration_open(event, detail):
        raise DomainError("Sự kiện hiện không mở đăng ký.")
    if not eligible(event, member):
        raise DomainError("Thành viên không thuộc đối tượng được tham gia.", 403)
    registration = EventRegistration.query.filter_by(event_id=event.id, user_id=member.id).first()
    if registration and registration.trang_thai != "cancelled":
        return registration, False
    state = "registered"  # Sự kiện không giới hạn số người
    if registration is None:
        registration = EventRegistration(event_id=event.id, user_id=member.id)
        db.session.add(registration)
    registration.trang_thai = state
    registration.dang_ky_luc = club_now()
    registration.huy_luc = None
    db.session.flush()
    audit(actor, "event_registration", registration, {"event_id": event.id, "member_id": member.id, "state": state})
    db.session.commit()
    return registration, True


def cancel_registration(actor, registration):
    """Hủy đăng ký (không cho hủy sau khi đã điểm danh); dữ liệu waitlist cũ (nếu có) vẫn được đôn
    lên.
    """
    require(approved(actor) and (actor.id == registration.user_id or can_manage_events(actor)))
    if registration.trang_thai == "cancelled":
        return None
    if registration.attendance and registration.attendance.trang_thai != "pending":
        raise DomainError("Không thể hủy đăng ký sau khi đã điểm danh.")
    freed_slot = registration.trang_thai == "registered"
    registration.trang_thai = "cancelled"
    registration.huy_luc = club_now()
    promoted = None
    if freed_slot:
        promoted = EventRegistration.query.filter_by(
            event_id=registration.event_id, trang_thai="waitlist"
        ).order_by(EventRegistration.dang_ky_luc, EventRegistration.id).first()
        if promoted:
            promoted.trang_thai = "registered"
    audit(actor, "event_registration_cancel", registration, {"promoted_id": promoted.id if promoted else None})
    db.session.commit()
    return promoted


def qr_token(actor, event):
    """Tạo token QR điểm danh có chữ ký (MANAGE_EVENTS)."""
    require(can_manage_events(actor))
    return _serializer().dumps({"event_id": event.id, "nonce": secrets.token_urlsafe(12)})


def qr_checkin(actor, token, now=None, expected_event_id=None):
    """Điểm danh bằng token QR: kiểm tra chữ ký, hạn 15 phút, đúng sự kiện, đã đăng ký, trong thời
    gian sự kiện; xác định đúng giờ/muộn.
    """
    require(approved(actor))
    try:
        payload = _serializer().loads(str(token or ""), max_age=QR_TTL_SECONDS)
    except SignatureExpired:
        raise DomainError("Mã QR đã hết hạn.") from None
    except BadSignature:
        raise DomainError("Mã QR không hợp lệ.") from None
    event = db.session.get(Event, payload.get("event_id"))
    if not event:
        raise DomainError("Không tìm thấy sự kiện của mã QR.", 404)
    if expected_event_id is not None and event.id != expected_event_id:
        raise DomainError("Mã QR không thuộc sự kiện này.")
    now = now or club_now()
    if event_is_terminal(event) or not (event.thoi_gian_bat_dau - timedelta(minutes=30) <= now <= event.thoi_gian_ket_thuc):
        raise DomainError("Chỉ được điểm danh trong thời gian diễn ra sự kiện.")
    registration = EventRegistration.query.filter_by(event_id=event.id, user_id=actor.id, trang_thai="registered").first()
    if not registration:
        raise DomainError("Bạn chưa đăng ký sự kiện này.")
    attendance = Attendance.query.filter_by(registration_id=registration.id).first()
    if (attendance and attendance.checkin_luc) or QRCheckinLog.query.filter_by(event_id=event.id, user_id=actor.id).first():
        raise DomainError("Bạn đã điểm danh sự kiện này rồi.")
    if attendance is None:
        attendance = Attendance(registration_id=registration.id)
        db.session.add(attendance)
    attendance.trang_thai = "late" if now > event.thoi_gian_bat_dau else "on_time"
    attendance.checkin_luc = club_now()
    attendance.checkin_boi_id = actor.id
    db.session.add(QRCheckinLog(event_id=event.id, user_id=actor.id, account_id=actor.id, checkin_luc=club_now()))
    db.session.flush()
    audit(actor, "qr_checkin", attendance, {"event_id": event.id})
    db.session.commit()
    return attendance


def manual_attendance(actor, registration, state, note=""):
    """Điểm danh thủ công cho lượt đăng ký chính thức (sự kiện đã kết thúc thì tính lại hoạt động)."""
    require(can_manage_events(actor))
    if state not in ATTENDANCE_STATES:
        raise DomainError("Trạng thái điểm danh không hợp lệ.")
    if registration.trang_thai != "registered":
        raise DomainError("Chỉ điểm danh thành viên đã đăng ký chính thức.")
    attendance = Attendance.query.filter_by(registration_id=registration.id).first()
    if attendance is None:
        attendance = Attendance(registration_id=registration.id)
        db.session.add(attendance)
    attendance.trang_thai = state
    attendance.checkin_boi_id = actor.id
    attendance.checkin_luc = club_now() if state in {"on_time", "late"} else None
    attendance.ghi_chu = str(note or "").strip()
    db.session.flush()
    # Sửa điểm danh sau khi sự kiện đã kết thúc → tính lại tình trạng hoạt động của kỳ.
    if registration.event.trang_thai == ENDED:
        refresh_for_event(registration.event)
    audit(actor, "attendance_manual", attendance, {"state": state})
    db.session.commit()
    return attendance


def feedback(actor, event, rating, comment=""):
    """Lưu/cập nhật đánh giá 1–5 sao của người đã tham gia, sau khi sự kiện kết thúc."""
    require(approved(actor))
    if event.trang_thai != ENDED:
        raise DomainError("Chỉ phản hồi sau khi sự kiện kết thúc.")
    registration = EventRegistration.query.filter_by(event_id=event.id, user_id=actor.id, trang_thai="registered").first()
    if not registration:
        raise DomainError("Bạn chưa tham gia sự kiện này.", 403)
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        raise DomainError("Đánh giá phải từ 1 đến 5.") from None
    if not 1 <= rating <= 5:
        raise DomainError("Đánh giá phải từ 1 đến 5.")
    row = EventFeedback.query.filter_by(event_id=event.id, user_id=actor.id).first()
    if row is None:
        row = EventFeedback(event_id=event.id, user_id=actor.id)
        db.session.add(row)
    row.danh_gia = rating
    row.binh_luan = str(comment or "").strip()
    db.session.flush()
    audit(actor, "event_feedback", row, {"event_id": event.id})
    db.session.commit()
    return row
