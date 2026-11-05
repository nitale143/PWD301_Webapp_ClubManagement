from datetime import datetime
import re
from flask import Blueprint, current_app, jsonify, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Event, EventDetail, EventRegistration, EventFeedback, Task, User, Ban
from app.forms import EventForm
from app.models import Permission
from app.permissions import approved_required, permission_required
from app.security import require_csrf
from app.mail import send_email
from app.services.email_templates import render_system_email
from app.services.event_status import (
    ENDED, allowed_targets, auto_close_at, change_status, display, initial_status,
    refresh as refresh_event_status,
)
from app.services.common import DomainError, club_now, parse_datetime
from app.services.access import can_manage_events
from app.services.events import (
    cancel_registration, eligible, event_is_terminal, feedback, manual_attendance,
    qr_checkin, register, registration_open,
)
from app.services.ctftime import CalendarUnavailable, SOURCE_URL, calendar_data, event_by_id
from app.timeutil import utcnow

event_bp = Blueprint("event", __name__)


def _can_manage_task(task):
    """Người đang đăng nhập được quản lý task (quyền MANAGE_EVENTS)."""
    return can_manage_events(current_user)


@event_bp.route("/event")
@login_required
@approved_required
def event_list():
    """Trang danh sách sự kiện (kèm tab lịch CTFtime)."""
    events = Event.query.order_by(Event.thoi_gian_bat_dau.desc()).all()
    return render_template("event.html", events=events)


@event_bp.get("/api/ctftime/events")
@login_required
@approved_required
def ctftime_events():
    """Fetch the public feed only when an approved member opens its tab."""
    try:
        payload = calendar_data()
    except CalendarUnavailable as exc:
        response = jsonify(error=str(exc), source_url=SOURCE_URL)
        response.status_code = 503
    else:
        response = jsonify(payload)
    response.headers["Cache-Control"] = "private, no-store"
    return response


@event_bp.post("/event/import/ctftime")
@login_required
@permission_required(Permission.MANAGE_EVENTS)
def ctftime_import():
    """Create an FIA event from a server-verified public CTFtime calendar row."""
    require_csrf()
    source_id = request.form.get("event_id", "").strip()
    if not re.fullmatch(r"[1-9]\d{0,11}", source_id):
        flash("Mã sự kiện CTFtime không hợp lệ.", "error")
        return redirect(url_for("event.event_list"))

    code = f"CTF-{source_id}"
    existing = Event.query.filter_by(ma_su_kien=code).first()
    if existing:
        flash("Sự kiện FIA này đã được tạo trước đó.", "success")
        return redirect(url_for("event.event_list"))

    try:
        source_event, feed = event_by_id(source_id)
    except CalendarUnavailable:
        flash("Chưa thể xác minh sự kiện với lịch CTFtime. Hãy thử lại sau.", "error")
        return redirect(url_for("event.event_list"))
    if not source_event or not feed:
        flash("Không tìm thấy sự kiện này trong lịch công khai CTFtime.", "error")
        return redirect(url_for("event.event_list"))

    try:
        starts = parse_datetime(source_event["start"], "Thời gian bắt đầu")
        ends = parse_datetime(source_event["finish"], "Thời gian kết thúc")
    except DomainError:
        flash("Thời gian từ lịch CTFtime không hợp lệ.", "error")
        return redirect(url_for("event.event_list"))

    now = club_now()
    if ends <= starts or ends <= now:
        flash("Sự kiện CTFtime đã kết thúc hoặc có thời gian không hợp lệ.", "error")
        return redirect(url_for("event.event_list"))

    event = Event(
        ten_su_kien=("FIA tham gia " + str(source_event["title"]).strip())[:150],
        ma_su_kien=code,
        thoi_gian_bat_dau=starts,
        thoi_gian_ket_thuc=ends,
        trang_thai=initial_status(starts, ends, now),
        tao_boi_id=current_user.id,
    )
    source_url = f"https://ctftime.org/event/{source_id}/"
    detail = EventDetail(
        loai="ctftime",
        noi_dung=f"FIA tham gia cuộc thi này, không phải đơn vị tổ chức.\nNguồn: {source_url}",
        dia_diem=(source_event.get("location") or "")[:300],
        phu_trach_id=current_user.id,
        tat_ca_thanh_vien=True,
    )
    try:
        db.session.add(event)
        db.session.flush()
        detail.event_id = event.id
        db.session.add(detail)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        duplicate = Event.query.filter_by(ma_su_kien=code).first()
        if duplicate:
            flash("Sự kiện FIA này đã được tạo trước đó.", "success")
            return redirect(url_for("event.event_list"))
        current_app.logger.exception("CTFtime event import failed while saving")
        flash("Chưa thể tạo sự kiện FIA. Hãy thử lại sau.", "error")
        return redirect(url_for("event.event_list"))

    if feed.get("stale"):
        flash("Đã tạo sự kiện FIA từ dữ liệu CTFtime đã lưu; lịch có thể chưa mới nhất.", "success")
    else:
        flash("Đã tạo sự kiện FIA từ CTFtime.", "success")
    return redirect(url_for("event.event_list"))


@event_bp.route("/event/new", methods=["GET", "POST"])
@login_required
@permission_required(Permission.MANAGE_EVENTS)
def event_new():
    """Tạo sự kiện bằng EventForm (Flask-WTF). Validate xong mới ghi database."""
    form = EventForm()
    if form.validate_on_submit():
        if form.thoi_gian_bat_dau.data <= club_now():
            form.thoi_gian_bat_dau.errors.append("Thời gian bắt đầu phải sau thời điểm hiện tại (giờ CLB).")
            return render_template("event_form.html", form=form)
        ev = Event(
            ten_su_kien=form.ten_su_kien.data,
            ma_su_kien=form.ma_su_kien.data,
            thoi_gian_bat_dau=form.thoi_gian_bat_dau.data,
            thoi_gian_ket_thuc=form.thoi_gian_ket_thuc.data,
            trang_thai=initial_status(form.thoi_gian_bat_dau.data),
            tao_boi_id=current_user.id,
        )
        db.session.add(ev)
        db.session.flush()
        db.session.add(EventDetail(event_id=ev.id, loai="other",
                                   noi_dung=form.noi_dung.data or "",
                                   dia_diem=form.dia_diem.data or "",
                                   han_dang_ky=form.han_dang_ky.data,
                                   phu_trach_id=current_user.id, tat_ca_thanh_vien=True))
        db.session.commit()
        flash("Đã tạo sự kiện. Tiếp tục thêm task con.", "success")
        return redirect(url_for("event.event_detail", event_id=ev.id))
    if request.method == "POST":
        for errors in form.errors.values():
            for message in errors:
                flash(message, "error")
        return render_template("event_form.html", form=form)
    return render_template("event_form.html", form=form)


@event_bp.route("/event/<int:event_id>")
@login_required
@approved_required
def event_detail(event_id):
    """Trang chi tiết sự kiện: đăng ký, điểm danh, phản hồi, danh sách task (thành viên chỉ thấy
    task của mình).
    """
    ev = db.get_or_404(Event, event_id)
    refresh_event_status(ev)
    bans = Ban.query.all()
    registration = EventRegistration.query.filter_by(event_id=event_id, user_id=current_user.id).first()
    registrations = EventRegistration.query.filter_by(event_id=event_id).order_by(EventRegistration.dang_ky_luc).all()
    detail = db.session.get(EventDetail, event_id)
    prior_feedback = EventFeedback.query.filter_by(event_id=event_id, user_id=current_user.id).first()
    return render_template(
        "event_detail.html", event=ev, bans=bans,
        is_bdh=can_manage_events(current_user),
        is_board=can_manage_events(current_user),
        registration=registration, registrations=registrations, detail=detail,
        can_register=registration_open(ev, detail) and eligible(ev, current_user),
        prior_feedback=prior_feedback,
        status=display(ev),
        status_actions=allowed_targets(ev) if can_manage_events(current_user) else {},
        auto_close_at=auto_close_at(ev),
    )


@event_bp.post("/event/<int:event_id>/register")
@login_required
@approved_required
def event_register(event_id):
    """Thành viên tự đăng ký tham gia sự kiện."""
    require_csrf()
    event = db.get_or_404(Event, event_id)
    try:
        registration, _created = register(current_user, event)
        flash("Đã đăng ký sự kiện." if registration.trang_thai == "registered" else "Đã vào danh sách chờ.", "success")
    except DomainError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("event.event_detail", event_id=event_id))


@event_bp.post("/event/<int:event_id>/cancel-registration")
@login_required
@approved_required
def event_cancel_registration(event_id):
    """Thành viên hủy đăng ký của mình."""
    require_csrf()
    registration = EventRegistration.query.filter_by(event_id=event_id, user_id=current_user.id).first_or_404()
    try:
        cancel_registration(current_user, registration)
        flash("Đã hủy đăng ký.", "success")
    except DomainError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("event.event_detail", event_id=event_id))


@event_bp.route("/event/<int:event_id>/checkin/<token>", methods=["GET", "POST"])
@login_required
@approved_required
def event_qr_checkin(event_id, token):
    """Trang xác nhận và xử lý điểm danh bằng link QR."""
    event = db.get_or_404(Event, event_id)
    if request.method == "POST":
        require_csrf()
        try:
            qr_checkin(current_user, token, expected_event_id=event_id)
            flash("Đã điểm danh thành công.", "success")
        except DomainError as exc:
            db.session.rollback()
            flash(str(exc), "error")
        return redirect(url_for("event.event_detail", event_id=event_id))
    return render_template("checkin_confirm.html", event=event, token=token)


@event_bp.post("/event/<int:event_id>/attendance/<int:registration_id>")
@login_required
@permission_required(Permission.MANAGE_EVENTS)
def event_manual_attendance(event_id, registration_id):
    """Người quản lý sự kiện điểm danh thủ công cho một lượt đăng ký."""
    require_csrf()
    registration = EventRegistration.query.filter_by(id=registration_id, event_id=event_id).first_or_404()
    try:
        manual_attendance(current_user, registration, request.form.get("status"), request.form.get("note", ""))
        flash("Đã cập nhật điểm danh.", "success")
    except DomainError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("event.event_detail", event_id=event_id))


@event_bp.post("/event/<int:event_id>/feedback")
@login_required
@approved_required
def event_feedback(event_id):
    """Thành viên gửi đánh giá sự kiện đã tham gia."""
    require_csrf()
    event = db.get_or_404(Event, event_id)
    try:
        feedback(current_user, event, request.form.get("rating"), request.form.get("comment", ""))
        flash("Đã lưu đánh giá.", "success")
    except DomainError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return redirect(url_for("event.event_detail", event_id=event_id))


@event_bp.route("/event/<int:event_id>/task/new", methods=["POST"])
@login_required
@permission_required(Permission.MANAGE_EVENTS)
def task_new(event_id):
    """Tạo task con. Người phụ trách BẮT BUỘC được chỉ định ngay từ đầu
    (không có cơ chế thành viên tự xung phong)."""
    ev = db.get_or_404(Event, event_id)
    if event_is_terminal(ev):
        abort(400)

    assignee_mssv = request.form.get("assignee_mssv", "").strip()
    assignee = User.query.filter(User.mssv == assignee_mssv.upper(), User.status == "approved",
                                 User.chuc_vu != "ADMIN").first()
    if not assignee:
        flash("Không tìm thấy thành viên với MSSV này. Task phải có người phụ trách ngay từ đầu.", "error")
        return redirect(url_for("event.event_detail", event_id=ev.id))

    ban_id = request.form.get("ban_id", type=int)
    if not ban_id or not db.session.get(Ban, ban_id):
        flash("Ban hoạt động không hợp lệ.", "error")
        return redirect(url_for("event.event_detail", event_id=ev.id))
    if not any(link.ban_id == ban_id for link in assignee.ban_links):
        flash("Người phụ trách không thuộc ban của task.", "error")
        return redirect(url_for("event.event_detail", event_id=ev.id))
    try:
        deadline = datetime.strptime(request.form.get("deadline", ""), "%Y-%m-%dT%H:%M")
    except ValueError:
        flash("Hạn task không hợp lệ.", "error")
        return redirect(url_for("event.event_detail", event_id=ev.id))
    ten_task = request.form.get("ten_task", "").strip()
    mo_ta = request.form.get("mo_ta", "").strip()
    if not ten_task or len(ten_task) > 150 or len(mo_ta) > 5000 or deadline > ev.thoi_gian_ket_thuc:
        flash("Tên task (tối đa 150 ký tự), mô tả (tối đa 5000 ký tự) hoặc hạn task không hợp lệ.", "error")
        return redirect(url_for("event.event_detail", event_id=ev.id))

    task = Task(
        event_id=ev.id,
        ten_task=ten_task,
        mo_ta=mo_ta,
        deadline=deadline,
        assignee_id=assignee.id,
        ban_id=ban_id,
        trang_thai="dang_lam",
    )
    db.session.add(task)
    db.session.commit()

    send_email(task.assignee.email, *render_system_email(
        "task_assigned", ten=task.assignee.ho_ten, task=task.ten_task, su_kien=ev.ten_su_kien,
        han=task.deadline.strftime('%d/%m/%Y %H:%M')))

    flash("Đã thêm task mới.", "success")
    return redirect(url_for("event.event_detail", event_id=ev.id))


@event_bp.route("/task/<int:task_id>/chi-dinh-lai", methods=["POST"])
@login_required
@permission_required(Permission.MANAGE_EVENTS)
def task_reassign(task_id):
    """Đổi người phụ trách một task đã tồn tại (vẫn là chỉ định, không phải xung phong)."""
    task = db.get_or_404(Task, task_id)
    if not _can_manage_task(task):
        abort(403)
    if event_is_terminal(task.event) or task.trang_thai in {"hoan_thanh", "huy"}:
        abort(400)
    mssv = request.form.get("assignee_mssv", "").strip()
    assignee = User.query.filter(User.mssv == mssv.upper(), User.status == "approved",
                                 User.chuc_vu != "ADMIN").first()
    if not assignee:
        flash("Không tìm thấy thành viên với MSSV này.", "error")
        return redirect(url_for("event.event_detail", event_id=task.event_id))
    if task.ban_id and not any(link.ban_id == task.ban_id for link in assignee.ban_links):
        flash("Người phụ trách mới không thuộc ban của task.", "error")
        return redirect(url_for("event.event_detail", event_id=task.event_id))

    task.assignee_id = assignee.id
    db.session.commit()

    send_email(assignee.email, *render_system_email(
        "task_reassigned", ten=assignee.ho_ten, task=task.ten_task,
        han=task.deadline.strftime('%d/%m/%Y %H:%M')))

    flash("Đã chỉ định lại người phụ trách task.", "success")
    return redirect(url_for("event.event_detail", event_id=task.event_id))


@event_bp.route("/task/<int:task_id>/nop-san-pham", methods=["POST"])
@login_required
@approved_required
def task_submit(task_id):
    """Chỉ người được giao task mới được nộp; nộp xong trạng thái chuyển sang cho_duyet."""
    task = db.get_or_404(Task, task_id)
    if task.assignee_id != current_user.id:
        abort(403)
    if task.trang_thai not in {"dang_lam", "lam_lai"} or event_is_terminal(task.event):
        abort(400)

    noi_dung_moi = request.form.get("noi_dung_nop", "").strip()
    # Nội dung nộp không thay đổi thì trạng thái giữ nguyên
    if len(noi_dung_moi) > 10000:
        flash("Nội dung nộp tối đa 10000 ký tự.", "error")
        return redirect(url_for("event.event_detail", event_id=task.event_id))
    if noi_dung_moi and noi_dung_moi != (task.noi_dung_nop or ""):
        task.noi_dung_nop = noi_dung_moi
        task.lan_nop_gan_nhat = utcnow()
        task.trang_thai = "cho_duyet"
        db.session.commit()

        nguoi_tao = db.session.get(User, task.event.tao_boi_id)
        if nguoi_tao:
            send_email(nguoi_tao.email, *render_system_email(
                "task_submitted", ten=nguoi_tao.ho_ten, nguoi_nop=current_user.ho_ten,
                task=task.ten_task, su_kien=task.event.ten_su_kien))

        flash("Đã nộp sản phẩm, chờ BĐH duyệt.", "success")
    else:
        flash("Chưa có thay đổi nội dung nộp, trạng thái giữ nguyên.", "error")
    return redirect(url_for("event.event_detail", event_id=task.event_id))


@event_bp.route("/task/<int:task_id>/cap-nhat-trang-thai", methods=["POST"])
@login_required
@permission_required(Permission.MANAGE_EVENTS)
def task_update_status(task_id):
    """Duyệt task: lam_lai (bắt buộc nhận xét) / hoan_thanh / huy. Không được đặt 'dang_lam' hay 'cho_duyet'."""
    task = db.get_or_404(Task, task_id)
    if not _can_manage_task(task):
        abort(403)
    if event_is_terminal(task.event) or task.trang_thai in {"hoan_thanh", "huy"}:
        abort(400)
    new_status = request.form.get("trang_thai")
    nhan_xet = request.form.get("nhan_xet_bdh", "").strip()

    if new_status not in ("lam_lai", "hoan_thanh", "huy"):
        abort(400)
    if new_status in {"lam_lai", "hoan_thanh"} and task.trang_thai != "cho_duyet":
        abort(400)
    if new_status == "lam_lai" and not nhan_xet:
        flash("Phải nhập nhận xét khi yêu cầu làm lại.", "error")
        return redirect(url_for("event.event_detail", event_id=task.event_id))

    task.trang_thai = new_status
    task.nhan_xet_bdh = nhan_xet or task.nhan_xet_bdh
    db.session.commit()

    status_label = {"lam_lai": "Làm lại", "hoan_thanh": "Hoàn thành", "huy": "Hủy"}[new_status]
    send_email(task.assignee.email, *render_system_email(
        "task_reviewed", ten=task.assignee.ho_ten, task=task.ten_task,
        su_kien=task.event.ten_su_kien, trang_thai=status_label,
        nhan_xet=nhan_xet or "(không có)"))

    flash("Đã cập nhật trạng thái task.", "success")
    return redirect(url_for("event.event_detail", event_id=task.event_id))


_STATUS_MESSAGES = {
    "dang_dien_ra": "Sự kiện đang diễn ra.",
    "da_ket_thuc": "Đã kết thúc sự kiện và cập nhật lại tình trạng hoạt động.",
    "cancelled": "Đã hủy sự kiện.",
}


def _change_event_status(event_id, new_status):
    ev = db.get_or_404(Event, event_id)
    reopened = ev.trang_thai == ENDED
    try:
        change_status(current_user, ev, new_status)
    except DomainError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    else:
        flash("Đã mở lại sự kiện; tình trạng hoạt động đã được tính lại." if reopened
              else _STATUS_MESSAGES[new_status], "success")
    return redirect(url_for("event.event_detail", event_id=ev.id))


@event_bp.route("/event/<int:event_id>/trang-thai", methods=["POST"])
@login_required
@permission_required(Permission.MANAGE_EVENTS)
def event_change_status(event_id):
    """BĐH chuyển trạng thái bằng tay: bắt đầu sớm, kết thúc, hủy, mở lại (xem event_status.MANUAL)."""
    require_csrf()
    return _change_event_status(event_id, request.form.get("trang_thai", ""))


@event_bp.route("/event/<int:event_id>/ket-thuc", methods=["POST"])
@login_required
@permission_required(Permission.MANAGE_EVENTS)
def event_end(event_id):
    """Kết thúc sự kiện (giữ đường dẫn cũ). Tình trạng hoạt động của kỳ được tính lại."""
    require_csrf()
    return _change_event_status(event_id, ENDED)
