import io
import os
import tempfile
import warnings
from datetime import date
from flask import Blueprint, abort, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from PIL import Image, ImageOps

from app import db
from app.models import User, MemberActivity, FundDue, MemberPlanningProfile
from app.services.activities import LABELS as ACTIVITY_LABELS, rules_note, summary as activity_summary
from app.forms import AvatarForm, ResendConfirmationForm
from app.permissions import approved_required
from app.services.access import can_issue_temp_password, can_view_member, can_view_all_members
from app.security import require_csrf

profile_bp = Blueprint("profile", __name__)


def _allowed_avatar(filename):
    """Đuôi file ảnh đại diện nằm trong ALLOWED_AVATAR_EXTENSIONS."""
    if not isinstance(filename, str) or not filename:
        return False
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext in current_app.config["ALLOWED_AVATAR_EXTENSIONS"]


def _sanitize_avatar(file, extension):
    """Kiểm tra ảnh thật bằng Pillow (đúng định dạng, không quá lớn, chống decompression bomb) và
    lưu lại bản sạch, bỏ metadata.
    """
    max_bytes = int(current_app.config.get("MAX_AVATAR_SIZE", 2 * 1024 * 1024))
    max_pixels = int(current_app.config.get("MAX_AVATAR_PIXELS", 4_000_000))
    raw = file.stream.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("too large")

    expected_formats = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "gif": "GIF", "webp": "WEBP"}
    expected_format = expected_formats.get(extension)
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != expected_format or image.width * image.height > max_pixels:
                raise ValueError("invalid image")
            image.verify()

        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            image = ImageOps.exif_transpose(image)
            image.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
            has_alpha = "A" in image.getbands() or "transparency" in image.info
            if expected_format in {"PNG", "WEBP"}:
                mode = "RGBA" if has_alpha else "RGB"
                clean = Image.frombytes(mode, image.size, image.convert(mode).tobytes())
            else:
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                clean = Image.frombytes("RGB", background.size, background.tobytes())

            output = io.BytesIO()
            save_options = {"quality": 90, "optimize": True} if expected_format in {"JPEG", "WEBP"} else {}
            clean.save(output, format=expected_format, **save_options)
            return output.getvalue()


@profile_bp.route("/profile")
@profile_bp.route("/profile/<int:user_id>")
@login_required
@approved_required
def view_profile(user_id=None):
    """Xem hồ sơ. Không truyền user_id → xem chính mình.

    Xem người khác cần quyền quản lý thành viên (MANAGE_MEMBERS).
    """
    user = db.get_or_404(User, user_id) if user_id else current_user
    if not can_view_member(current_user, user):
        abort(403)
    is_self = user.id == current_user.id
    can_see_full = is_self or can_view_all_members(current_user)

    activities = (
        MemberActivity.query.filter_by(user_id=user.id)
        .order_by(MemberActivity.period_id.desc())
        .all()
    )
    dues = FundDue.query.filter_by(user_id=user.id).all() if can_see_full else []

    return render_template(
        "profile.html",
        member=user,
        is_self=is_self,
        can_see_full=can_see_full,
        activities=activities,
        activity=activity_summary(user),
        activity_labels=ACTIVITY_LABELS,
        activity_rules=rules_note(),
        dues=dues,
        planning_profile=db.session.get(MemberPlanningProfile, user.id) if is_self else None,
        avatar_form=AvatarForm(),
        resend_form=ResendConfirmationForm(),
        can_issue_temp=(not is_self and can_issue_temp_password(current_user, user)),
    )


@profile_bp.route("/profile/task-preferences", methods=["POST"])
@login_required
@approved_required
def task_preferences():
    """Members control the information used by AI task suggestions."""
    require_csrf()
    skills = list(dict.fromkeys(
        skill.strip().casefold() for skill in request.form.get("skills", "").split(",")
        if skill.strip()
    ))
    try:
        capacity = int(request.form.get("max_active_tasks", "3"))
        until_raw = request.form.get("unavailable_until", "").strip()
        unavailable_until = date.fromisoformat(until_raw) if until_raw else None
    except ValueError:
        flash("Thông tin nhận task không hợp lệ.", "error")
        return redirect(url_for("profile.view_profile"))
    if (len(skills) > 10 or any(not 2 <= len(skill) <= 30 for skill in skills)
            or not 1 <= capacity <= 10):
        flash("Tối đa 10 kỹ năng và 1–10 task đang làm.", "error")
        return redirect(url_for("profile.view_profile"))
    profile = db.session.get(MemberPlanningProfile, current_user.id)
    if profile is None:
        profile = MemberPlanningProfile(user_id=current_user.id)
        db.session.add(profile)
    profile.skills = skills
    profile.max_active_tasks = capacity
    profile.unavailable_until = unavailable_until
    db.session.commit()
    flash("Đã cập nhật khả năng nhận task.", "success")
    return redirect(url_for("profile.view_profile"))


@profile_bp.route("/profile/avatar", methods=["POST"])
@login_required
@approved_required
def upload_avatar():
    """Upload ảnh đại diện (AvatarForm: FileRequired + FileAllowed), rồi kiểm tra
    nội dung thật bằng Pillow và lưu lại ảnh đã làm sạch."""
    form = AvatarForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            for message in errors:
                flash(message, "error")
        return redirect(url_for("profile.view_profile"))
    file = form.avatar.data
    if not _allowed_avatar(file.filename):
        flash("Chỉ chấp nhận file ảnh (png, jpg, jpeg, gif, webp).", "error")
        return redirect(url_for("profile.view_profile"))

    ext = file.filename.rsplit(".", 1)[-1].lower()
    try:
        image_bytes = _sanitize_avatar(file, ext)
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        flash("Ảnh không hợp lệ hoặc vượt quá giới hạn tải lên.", "error")
        return redirect(url_for("profile.view_profile"))

    upload_folder = os.path.realpath(current_app.config["UPLOAD_FOLDER"])
    os.makedirs(upload_folder, exist_ok=True)
    filename = f"user_{current_user.id}.{ext}"
    filepath = os.path.join(upload_folder, filename)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=upload_folder, prefix=f".user_{current_user.id}.",
                                         suffix=".tmp", delete=False) as temporary:
            temporary_path = temporary.name
            temporary.write(image_bytes)
        os.replace(temporary_path, filepath)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)

    current_user.avatar_url = f"/static/uploads/avatars/{filename}"
    db.session.commit()

    flash("Đã cập nhật ảnh đại diện.", "success")
    return redirect(url_for("profile.view_profile"))
