"""Trạng thái sự kiện – nơi DUY NHẤT được đổi `Event.trang_thai`.

Bốn trạng thái lưu trong database:

    sap_dien_ra ──(tới giờ bắt đầu)──► dang_dien_ra ──(giờ kết thúc + EVENT_AUTO_CLOSE_HOURS)──► da_ket_thuc
         │                                  │
         └────────────── cancelled ◄────────┘          (chỉ người quản lý sự kiện hủy, không tự động)

Hai cách chuyển:
  - Tự động (`sync_statuses`): chỉ đi tới, theo giờ. Chạy bởi job scheduler 5 phút/lần, khi có
    người mở web (tối đa 1 lần / EVENT_STATUS_SYNC_SECONDS mỗi tiến trình) và lệnh
    `flask sync-event-status`.
  - Bằng tay (`change_status`, quyền MANAGE_EVENTS): bắt đầu sớm, kết thúc sớm, hủy, mở lại sự kiện
    đã kết thúc. Mở lại thì hệ thống tự kết thúc lại sau EVENT_AUTO_CLOSE_HOURS tính từ lúc mở.

Khi sự kiện vào hoặc rời "da_ket_thuc", tình trạng hoạt động của kỳ được tính lại.
"Chờ kết thúc" (`display`) chỉ là nhãn hiển thị: đang diễn ra nhưng đã qua giờ kết thúc.
"""
from datetime import timedelta
from time import monotonic

from flask import current_app

from app import db
from app.config import Config
from app.models import Attendance, Event, EventRegistration
from .access import can_manage_events, require
from .activities import refresh_for_event
from .common import DomainError, audit, club_now

UPCOMING, ONGOING, ENDED, CANCELLED = "sap_dien_ra", "dang_dien_ra", "da_ket_thuc", "cancelled"
STATUSES = (UPCOMING, ONGOING, ENDED, CANCELLED)
TERMINAL = frozenset({ENDED, CANCELLED})
OPEN = frozenset({UPCOMING, ONGOING})
WAITING_CLOSE = "cho_ket_thuc"
LABELS = Config.EVENT_STATUS_LABELS
TONES = {UPCOMING: "indigo", ONGOING: "success", WAITING_CLOSE: "warning", ENDED: "neutral",
         CANCELLED: "danger"}

# Chuyển bằng tay được phép: trạng thái hiện tại → {trạng thái mới: nhãn nút}
MANUAL = {
    UPCOMING: {ONGOING: "Bắt đầu ngay", ENDED: "Kết thúc sự kiện", CANCELLED: "Hủy sự kiện"},
    ONGOING: {ENDED: "Kết thúc sự kiện", CANCELLED: "Hủy sự kiện"},
    ENDED: {ONGOING: "Mở lại sự kiện"},
    CANCELLED: {},
}


# ---------------------------------------------------------------- tính theo giờ
def _grace():
    return timedelta(hours=current_app.config.get("EVENT_AUTO_CLOSE_HOURS", 24))


def initial_status(start, end=None, now=None):
    """Trạng thái lúc tạo sự kiện: chưa tới giờ bắt đầu là sắp diễn ra, ngược lại đang diễn ra
    (lần rà soát kế tiếp sẽ kết thúc nếu đã quá hạn)."""
    return UPCOMING if (now or club_now()) < start else ONGOING


def auto_close_at(event):
    """Thời điểm hệ thống tự kết thúc sự kiện đang mở."""
    base = event.thoi_gian_ket_thuc
    if event.trang_thai_doi_tay_luc and event.trang_thai_doi_tay_luc > base:
        base = event.trang_thai_doi_tay_luc
    return base + _grace()


def due_status(event, now=None):
    """Trạng thái hệ thống sẽ tự chuyển tới (None nếu không cần đổi). Chỉ đi tới, không lùi."""
    now = now or club_now()
    if event.trang_thai not in OPEN:
        return None
    if now >= auto_close_at(event) and now >= event.thoi_gian_bat_dau:
        return ENDED
    if event.trang_thai == UPCOMING and now >= event.thoi_gian_bat_dau:
        return ONGOING
    return None


def display(event, now=None):
    """{code, label, tone} để hiển thị – dùng chung cho mọi trang, AI và API."""
    now = now or club_now()
    code = event.trang_thai if event.trang_thai in STATUSES else UPCOMING
    if code == ONGOING and now > event.thoi_gian_ket_thuc:
        code = WAITING_CLOSE
    return {"code": code, "label": LABELS[code], "tone": TONES[code]}


def is_terminal(event):
    """Đã kết thúc hoặc đã hủy: khóa task, đăng ký."""
    return bool(event and event.trang_thai in TERMINAL)


# ---------------------------------------------------------------- đổi trạng thái
def _apply(event, new_status, actor, source):
    """Ghi trạng thái bằng UPDATE có điều kiện (hai tiến trình không chuyển trùng) rồi xử lý hệ quả."""
    old_status = event.trang_thai
    values = {"trang_thai": new_status}
    if source == "manual":
        values["trang_thai_doi_tay_luc"] = club_now()
    result = db.session.execute(
        db.update(Event).where(Event.id == event.id, Event.trang_thai == old_status).values(**values)
    )
    if result.rowcount != 1:
        db.session.refresh(event)
        return False
    for key, value in values.items():
        setattr(event, key, value)
    audit(actor, "event_status", event, {"from": old_status, "to": new_status, "source": source})
    if ENDED in (old_status, new_status):
        refresh_for_event(event)
    return True


def allowed_targets(event):
    """{trạng thái mới: nhãn nút} người quản lý sự kiện được chọn."""
    return dict(MANUAL.get(event.trang_thai, {}))


def refresh(event, now=None):
    """Áp ngay chuyển trạng thái tự động đang đến hạn của MỘT sự kiện (không chờ lượt rà soát).
    Commit nếu có thay đổi; trả về True khi đã đổi."""
    target = due_status(event, now)
    if target and _apply(event, target, None, "auto"):
        db.session.commit()
        return True
    return False


def change_status(actor, event, new_status):
    """Người quản lý sự kiện (MANAGE_EVENTS) chuyển trạng thái bằng tay. Commit và trả về sự kiện."""
    require(can_manage_events(actor))
    refresh(event)   # trạng thái theo giờ phải đúng trước khi xét chuyển tay
    if new_status not in allowed_targets(event):
        raise DomainError("Không thể chuyển sự kiện từ \"{}\" sang \"{}\".".format(
            LABELS.get(event.trang_thai, event.trang_thai), LABELS.get(new_status, new_status)))
    if new_status == CANCELLED and Attendance.query.join(EventRegistration).filter(
            EventRegistration.event_id == event.id, Attendance.trang_thai != "pending").first():
        raise DomainError("Không thể hủy sự kiện đã điểm danh. Hãy kết thúc sự kiện.")
    if not _apply(event, new_status, actor, "manual"):
        raise DomainError("Trạng thái sự kiện vừa thay đổi. Hãy tải lại trang.", 409)
    db.session.commit()
    return event


def reschedule(event, now=None):
    """Gọi sau khi sửa giờ một sự kiện đang mở: đặt lại trạng thái theo giờ mới (chưa commit)."""
    if event.trang_thai in OPEN:
        event.trang_thai = initial_status(event.thoi_gian_bat_dau, event.thoi_gian_ket_thuc, now)
        event.trang_thai_doi_tay_luc = None


def sync_statuses(now=None):
    """Rà soát mọi sự kiện đang mở và tự chuyển theo giờ. Commit; trả về số sự kiện đã đổi."""
    now = now or club_now()
    candidates = Event.query.filter(Event.trang_thai.in_(tuple(OPEN)),
                                    Event.thoi_gian_bat_dau <= now).all()
    changed = 0
    for event in candidates:
        target = due_status(event, now)
        if target and _apply(event, target, None, "auto"):
            changed += 1
    db.session.commit()
    return changed


_LAST_SYNC = "event_status_last_sync"


def sync_if_due(app):
    """Rà soát khi có request, tối đa 1 lần mỗi EVENT_STATUS_SYNC_SECONDS cho mỗi tiến trình.
    Lỗi không làm hỏng request (chỉ ghi log)."""
    if not app.config.get("EVENT_STATUS_AUTO_SYNC", True):
        return
    state = app.extensions.setdefault(_LAST_SYNC, {"at": None})
    if state["at"] is not None and monotonic() - state["at"] < app.config["EVENT_STATUS_SYNC_SECONDS"]:
        return
    state["at"] = monotonic()
    try:
        sync_statuses()
    except Exception:  # noqa: BLE001 – rà soát nền, không chặn người dùng
        db.session.rollback()
        app.logger.exception("Không rà soát được trạng thái sự kiện")
