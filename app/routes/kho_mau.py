"""Kho mẫu: mẫu email (hệ thống + của CLB), mẫu sự kiện và mẫu chia task cho AI.

- Mẫu email: quyền MANAGE_TEMPLATES (Quản trị hệ thống, CN, PCN).
- Mẫu sự kiện / mẫu chia task: quyền MANAGE_EVENTS (4 chức vụ BĐH + Quản trị hệ thống).

Nháp email / đề xuất đã tạo trước đó KHÔNG bị đổi khi sửa mẫu (nháp giữ nội dung đã xem trước).
"""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import db
from app.models import EmailTemplate, Permission, ProposalTemplate
from app.permissions import any_permission_required, permission_required
from app.security import require_csrf
from app.services.access import has_permission
from app.services.common import DomainError
from app.services.email_templates import (
    PLACEHOLDERS, SYSTEM_EMAILS, reset_system_template, save_template, set_template_active,
)
from app.services.proposal_templates import (
    KINDS, save_proposal_template, set_proposal_template_active, task_lines,
)

kho_mau_bp = Blueprint("kho_mau", __name__, url_prefix="/kho-mau")


def _back(tab):
    """Quay lại đúng tab của trang Kho mẫu."""
    return redirect(url_for("kho_mau.index", tab=tab))


def _run(action, success, tab):
    """Chạy một thao tác lưu; lỗi nghiệp vụ → rollback và báo lỗi."""
    require_csrf()
    try:
        action()
        flash(success, "success")
    except DomainError as exc:
        db.session.rollback()
        flash(str(exc), "error")
    return _back(tab)


@kho_mau_bp.route("")
@login_required
@any_permission_required(Permission.MANAGE_TEMPLATES, Permission.MANAGE_EVENTS)
def index():
    """Trang Kho mẫu với 3 tab: Mẫu email · Mẫu sự kiện · Mẫu chia task."""
    can_email = has_permission(current_user, Permission.MANAGE_TEMPLATES)
    can_events = has_permission(current_user, Permission.MANAGE_EVENTS)
    tabs = ([("email", "Mẫu email")] if can_email else []) + (
        [("event", "Mẫu sự kiện"), ("task", "Mẫu chia task")] if can_events else [])
    tab = request.args.get("tab") or tabs[0][0]
    if tab not in {key for key, _label in tabs}:
        tab = tabs[0][0]
    system_emails = (EmailTemplate.query.filter(EmailTemplate.system_key.isnot(None))
                     .order_by(EmailTemplate.id).all() if can_email else [])
    club_emails = (EmailTemplate.query.filter(EmailTemplate.system_key.is_(None))
                   .order_by(EmailTemplate.name).all() if can_email else [])
    proposals = {kind: (ProposalTemplate.query.filter_by(kind=kind)
                        .order_by(ProposalTemplate.is_default.desc(), ProposalTemplate.name).all()
                        if can_events else []) for kind in KINDS}
    return render_template("kho_mau.html", tab=tab, tabs=tabs, system_emails=system_emails,
                           club_emails=club_emails, proposals=proposals, kinds=KINDS,
                           system_specs=SYSTEM_EMAILS, club_placeholders=sorted(PLACEHOLDERS),
                           task_lines=task_lines)


# ---------------- Mẫu email ----------------
@kho_mau_bp.post("/email")
@login_required
@permission_required(Permission.MANAGE_TEMPLATES)
def email_create():
    """Tạo mẫu email của CLB."""
    form = request.form
    return _run(lambda: save_template(current_user, form.get("name"), form.get("subject"),
                                      form.get("body"), keywords=form.get("keywords"),
                                      description=form.get("description")),
                "Đã lưu mẫu email vào kho; chưa gửi email nào.", "email")


@kho_mau_bp.post("/email/<int:template_id>")
@login_required
@permission_required(Permission.MANAGE_TEMPLATES)
def email_update(template_id):
    """Sửa mẫu email (mẫu hệ thống: chỉ tiêu đề/nội dung)."""
    template = db.get_or_404(EmailTemplate, template_id)
    form = request.form
    return _run(lambda: save_template(current_user, form.get("name"), form.get("subject"),
                                      form.get("body"), template, keywords=form.get("keywords"),
                                      description=form.get("description")),
                "Đã cập nhật mẫu email. Nháp cũ vẫn giữ nội dung đã xem trước.", "email")


@kho_mau_bp.post("/email/<int:template_id>/active")
@login_required
@permission_required(Permission.MANAGE_TEMPLATES)
def email_active(template_id):
    """Bật/tắt mẫu email của CLB."""
    template = db.get_or_404(EmailTemplate, template_id)
    return _run(lambda: set_template_active(current_user, template, request.form.get("active") == "yes"),
                "Đã cập nhật trạng thái mẫu email.", "email")


@kho_mau_bp.post("/email/<int:template_id>/reset")
@login_required
@permission_required(Permission.MANAGE_TEMPLATES)
def email_reset(template_id):
    """Khôi phục nội dung mặc định của mẫu email hệ thống."""
    template = db.get_or_404(EmailTemplate, template_id)
    return _run(lambda: reset_system_template(current_user, template),
                "Đã khôi phục nội dung mặc định.", "email")


# ---------------- Mẫu sự kiện / mẫu chia task ----------------
@kho_mau_bp.post("/<kind>")
@login_required
@permission_required(Permission.MANAGE_EVENTS)
def proposal_create(kind):
    """Tạo mẫu sự kiện (kind=event) hoặc mẫu chia task (kind=task)."""
    if kind not in KINDS:
        abort(404)
    return _run(lambda: save_proposal_template(current_user, kind, request.form),
                f"Đã lưu {KINDS[kind].lower()} vào kho.", kind)


@kho_mau_bp.post("/<kind>/<int:template_id>")
@login_required
@permission_required(Permission.MANAGE_EVENTS)
def proposal_update(kind, template_id):
    """Sửa mẫu sự kiện / mẫu chia task."""
    template = db.get_or_404(ProposalTemplate, template_id)
    if template.kind != kind:
        abort(404)
    return _run(lambda: save_proposal_template(current_user, kind, request.form, template),
                "Đã cập nhật mẫu. Đề xuất cũ vẫn giữ nội dung đã tạo.", kind)


@kho_mau_bp.post("/<kind>/<int:template_id>/active")
@login_required
@permission_required(Permission.MANAGE_EVENTS)
def proposal_active(kind, template_id):
    """Bật/tắt mẫu sự kiện / mẫu chia task."""
    template = db.get_or_404(ProposalTemplate, template_id)
    if template.kind != kind:
        abort(404)
    return _run(lambda: set_proposal_template_active(current_user, template,
                                                     request.form.get("active") == "yes"),
                "Đã cập nhật trạng thái mẫu.", kind)
