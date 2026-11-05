"""Tình trạng hoạt động của thành viên – nguồn DUY NHẤT cho toàn hệ thống.

Mọi nơi hiển thị hoặc lọc theo tình trạng hoạt động (trang Thành viên, Tổng quan, hồ sơ,
Excel, API, trợ lý AI) đều đọc qua module này.

Công thức cho một thành viên trong một kỳ (FundPeriod):
  - Tỉ lệ task      = task hoàn thành / task được giao (không tính task đã hủy).
  - Tỉ lệ điểm danh = số sự kiện có mặt (đúng giờ hoặc đi muộn) / số sự kiện phải tham gia.
    Sự kiện phải tham gia = sự kiện mở cho mọi người hoặc cho ban của thành viên.
    Vắng có phép không tính vào mẫu số; không đăng ký hoặc chưa điểm danh tính là vắng.
  - Điểm = trọng số trong Config.ACTIVITY_WEIGHTS (mặc định 50% task + 50% điểm danh).
    Thiếu một phần thì phần còn lại chiếm 100%; thiếu cả hai → "Chưa xếp loại".
  - Chỉ tính task và sự kiện thuộc các sự kiện ĐÃ KẾT THÚC, có ngày kết thúc nằm trong kỳ.
  - Thành viên được duyệt trong kỳ nào thì kỳ đó "Chưa xếp loại" (User.ky_tham_gia_id).
  - Thành viên đang tạm nghỉ (MemberRecord.tinh_trang = paused) hiển thị "Tạm nghỉ".

Kết quả được lưu ở bảng MemberActivity (một dòng cho mỗi thành viên mỗi kỳ) và được tính lại
khi: sự kiện vào hoặc rời trạng thái đã kết thúc (BĐH bấm hoặc hệ thống tự chuyển, xem
event_status.py), sửa điểm danh của sự kiện đã kết thúc, hoặc chạy `flask recompute-activity`. Trang hiển thị dùng kỳ hiện tại (FundPeriod.is_current).
"""
from app import db
from app.config import Config
from app.models import (
    Attendance, Event, EventDetail, EventRegistration, EventTargetBan, FundPeriod,
    MemberActivity, MemberRecord, Task, User,
)
from app.timeutil import club_now

LABELS = Config.ACTIVITY_LABELS
PAUSED = "tam_nghi"
UNRATED = "chua_xep_loai"
# Các mức được coi là "đang hoạt động" / "cần theo dõi" trên trang tổng hợp.
ACTIVE_LEVELS = ("tich_cuc", "hoat_dong")
FOLLOW_UP_LEVELS = ("it_hoat_dong", "khong_hoat_dong")


# ---------------------------------------------------------------- kỳ
def current_period():
    """Kỳ đang mở (FundPeriod.is_current) hoặc None."""
    return FundPeriod.query.filter_by(is_current=True).first()


def period_of(day):
    """Kỳ có khoảng [ngày bắt đầu, ngày kết thúc] chứa `day` (kỳ chưa có ngày kết thúc = còn mở)."""
    return FundPeriod.query.filter(
        FundPeriod.ngay_bat_dau <= day,
        db.or_(FundPeriod.ngay_ket_thuc.is_(None), FundPeriod.ngay_ket_thuc >= day),
    ).order_by(FundPeriod.ngay_bat_dau.desc()).first()


def _in_period(event, period):
    """Sự kiện kết thúc trong kỳ."""
    day = event.thoi_gian_ket_thuc.date()
    return period.ngay_bat_dau <= day and (period.ngay_ket_thuc is None or day <= period.ngay_ket_thuc)


def _ended_events(period):
    """Các sự kiện đã kết thúc thuộc kỳ."""
    rows = Event.query.filter(Event.trang_thai == "da_ket_thuc").all()
    return [event for event in rows if _in_period(event, period)]


def _target_bans(event_ids):
    """{event_id: tập ban áp dụng} – chỉ cho sự kiện không mở cho mọi người."""
    if not event_ids:
        return {}
    limited = {row.event_id for row in EventDetail.query.filter(
        EventDetail.event_id.in_(event_ids), EventDetail.tat_ca_thanh_vien.is_(False))}
    targets = {event_id: set() for event_id in limited}
    for row in EventTargetBan.query.filter(EventTargetBan.event_id.in_(limited or [-1])):
        targets[row.event_id].add(row.ban_id)
    return targets


# ---------------------------------------------------------------- tính toán
def score_to_level(score):
    """Đổi điểm 0–100 thành mã mức theo Config.ACTIVITY_THRESHOLDS; None → chưa xếp loại."""
    if score is None:
        return UNRATED
    for threshold, level in Config.ACTIVITY_THRESHOLDS:
        if threshold > 0 and score >= threshold:
            return level
    return "khong_hoat_dong"


def combine(task_rate, attendance_rate):
    """Gộp hai tỉ lệ (%, có thể None) theo trọng số; thiếu một phần thì dùng phần còn lại."""
    weights = Config.ACTIVITY_WEIGHTS
    parts = [(rate, weights[name]) for name, rate in (("task", task_rate), ("attendance", attendance_rate))
             if rate is not None and weights[name] > 0]
    if not parts:
        return None
    return round(sum(rate * weight for rate, weight in parts) / sum(weight for _r, weight in parts), 1)


def _rate(done, total):
    return round(done * 100 / total, 1) if total else None


def compute(member, period, events=None, targets=None):
    """Số liệu hoạt động của một thành viên trong một kỳ (không ghi database)."""
    if events is None:
        events = _ended_events(period)
    if targets is None:
        targets = _target_bans([event.id for event in events])
    if member.ky_tham_gia_id == period.id:
        return {"task_total": 0, "task_done": 0, "event_total": 0, "event_present": 0,
                "task_rate": None, "attendance_rate": None, "score": None, "level": UNRATED,
                "new_member": True}
    event_ids = [event.id for event in events]

    tasks = Task.query.filter(Task.assignee_id == member.id, Task.event_id.in_(event_ids or [-1]),
                              Task.trang_thai != "huy").all()
    task_done = sum(task.trang_thai == "hoan_thanh" for task in tasks)

    member_bans = {link.ban_id for link in member.ban_links}
    required = [event_id for event_id in event_ids
                if event_id not in targets or targets[event_id] & member_bans]
    states = dict(
        db.session.query(EventRegistration.event_id, Attendance.trang_thai)
        .join(Attendance, Attendance.registration_id == EventRegistration.id)
        .filter(EventRegistration.user_id == member.id,
                EventRegistration.event_id.in_(required or [-1]),
                EventRegistration.trang_thai == "registered")
        .all()
    )
    counted = [event_id for event_id in required if states.get(event_id) != "excused"]
    present = sum(states.get(event_id) in {"on_time", "late"} for event_id in counted)

    task_rate = _rate(task_done, len(tasks))
    attendance_rate = _rate(present, len(counted))
    score = combine(task_rate, attendance_rate)
    return {"task_total": len(tasks), "task_done": task_done, "event_total": len(counted),
            "event_present": present, "task_rate": task_rate, "attendance_rate": attendance_rate,
            "score": score, "level": score_to_level(score), "new_member": False}


def _save(member, period, data):
    row = MemberActivity.query.filter_by(user_id=member.id, period_id=period.id).first()
    if row is None:
        row = MemberActivity(user_id=member.id, period_id=period.id)
        db.session.add(row)
    row.tong_task = data["task_total"]
    row.task_hoan_thanh = data["task_done"]
    row.tong_su_kien = data["event_total"]
    row.su_kien_co_mat = data["event_present"]
    row.diem = data["score"]
    row.trang_thai = data["level"]
    row.cap_nhat_luc = club_now()
    return row


def _members():
    """Thành viên được xếp loại: đã duyệt, chưa lưu trữ/rời CLB, không phải tài khoản quản trị."""
    from .access import active_members
    return active_members().filter(db.or_(MemberRecord.tinh_trang.is_(None),
                                          MemberRecord.tinh_trang != "left")).all()


def refresh_period(period):
    """Tính lại và lưu tình trạng của mọi thành viên trong một kỳ. Trả về số dòng đã ghi."""
    if period is None:
        return 0
    events = _ended_events(period)
    targets = _target_bans([event.id for event in events])
    members = _members()
    for member in members:
        _save(member, period, compute(member, period, events, targets))
    db.session.flush()
    return len(members)


def refresh_for_event(event):
    """Tính lại kỳ chứa ngày kết thúc của sự kiện. Gọi khi sự kiện vào/rời trạng thái đã kết thúc
    hoặc khi dữ liệu của sự kiện đã kết thúc thay đổi (vd. sửa điểm danh)."""
    return refresh_period(period_of(event.thoi_gian_ket_thuc.date()))


def refresh_all():
    """Tính lại mọi kỳ (dùng sau khi nâng cấp hoặc sửa dữ liệu hàng loạt)."""
    return sum(refresh_period(period) for period in FundPeriod.query.order_by(FundPeriod.id).all())


# ---------------------------------------------------------------- đọc
def _paused(member):
    record = db.session.get(MemberRecord, member.id)
    return bool(record and record.tinh_trang == "paused")


def stored(member, period=None):
    """Dòng MemberActivity của kỳ (mặc định kỳ hiện tại) hoặc None."""
    period = period or current_period()
    if period is None:
        return None
    return MemberActivity.query.filter_by(user_id=member.id, period_id=period.id).first()


def level_of(member, period=None):
    """Mã tình trạng hiển thị của thành viên: tạm nghỉ ghi đè, chưa có dữ liệu = chưa xếp loại."""
    if _paused(member):
        return PAUSED
    row = stored(member, period)
    return row.trang_thai if row and row.trang_thai in LABELS else UNRATED


def levels_for(members, period=None):
    """{user_id: mã tình trạng} cho nhiều thành viên bằng 2 truy vấn (dùng cho bảng danh sách)."""
    period = period or current_period()
    ids = [member.id for member in members]
    if not ids:
        return {}
    paused = {row.user_id for row in MemberRecord.query.filter(
        MemberRecord.user_id.in_(ids), MemberRecord.tinh_trang == "paused")}
    rows = {}
    if period is not None:
        rows = {row.user_id: row.trang_thai for row in MemberActivity.query.filter(
            MemberActivity.period_id == period.id, MemberActivity.user_id.in_(ids))}
    return {user_id: PAUSED if user_id in paused else (rows.get(user_id) if rows.get(user_id) in LABELS
                                                         else UNRATED)
            for user_id in ids}


def label_of(member, period=None):
    """Nhãn tiếng Việt của tình trạng hiện tại."""
    return LABELS[level_of(member, period)]


def summary(member, period=None):
    """Số liệu đã lưu để hiển thị / trả lời AI / API (không tính lại)."""
    period = period or current_period()
    row = stored(member, period)
    level = level_of(member, period)
    task_total = row.tong_task if row else 0
    task_done = row.task_hoan_thanh if row else 0
    event_total = row.tong_su_kien if row else 0
    event_present = row.su_kien_co_mat if row else 0
    return {
        "period": period.ten_ky if period else None,
        "level": level, "label": LABELS[level],
        "score": row.diem if row else None,
        "task_total": task_total, "task_done": task_done, "task_rate": _rate(task_done, task_total),
        "event_total": event_total, "event_present": event_present,
        "attendance_rate": _rate(event_present, event_total),
        "updated_at": row.cap_nhat_luc if row else None,
    }


def level_filter(level, period=None):
    """Điều kiện SQL lọc User theo mã tình trạng của kỳ hiện tại (dùng subquery, không JOIN)."""
    period = period or current_period()
    paused_ids = db.session.query(MemberRecord.user_id).filter(MemberRecord.tinh_trang == "paused")
    if level == PAUSED:
        return User.id.in_(paused_ids)
    period_id = period.id if period else -1
    if level == UNRATED:
        rated = db.session.query(MemberActivity.user_id).filter(
            MemberActivity.period_id == period_id, MemberActivity.trang_thai != UNRATED)
        return db.and_(User.id.notin_(paused_ids), User.id.notin_(rated))
    levels = level if isinstance(level, (list, tuple, set)) else [level]
    matched = db.session.query(MemberActivity.user_id).filter(
        MemberActivity.period_id == period_id, MemberActivity.trang_thai.in_(levels))
    return db.and_(User.id.notin_(paused_ids), User.id.in_(matched))


def rules_note():
    """Một câu giải thích công thức đang áp dụng (cho người đọc / trợ lý AI)."""
    weights = Config.ACTIVITY_WEIGHTS
    levels = {level: threshold for threshold, level in Config.ACTIVITY_THRESHOLDS}
    return (f"Cách xếp loại: điểm = {weights['task']}% tỉ lệ task hoàn thành + "
            f"{weights['attendance']}% tỉ lệ có mặt ở các sự kiện đã kết thúc trong kỳ; "
            f"Tích cực ≥ {levels['tich_cuc']:g}, Hoạt động ≥ {levels['hoat_dong']:g}, "
            "trên 0 là Ít hoạt động, 0 là Không hoạt động.")
