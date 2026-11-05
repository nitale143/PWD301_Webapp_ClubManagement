"""Khu vực Ban điều hành: duyệt thành viên, phân vai trò, nhường chức vụ, cấp mật khẩu tạm.

Mọi route đều theo thứ tự decorator: route → login_required → (fresh_login_required)
→ permission_required (Ch7 – slide 85).
"""
import secrets

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, fresh_login_required, login_required

from app import db
from app.mail import send_email
from app.models import (
    DEFAULT_ROLES, SYSTEM_ADMIN_ROLE, Ban, FundPeriod, Permission, User, UserBan, role_rank,
)
from app.permissions import any_permission_required, can_assign_role, permission_required
from app.services.access import (
    MEMBER_ROLE, approved, can_assign_role_to, can_issue_temp_password, set_member_role_safely,
)
from app.services.common import audit, member_record
from app.services.email_templates import render_system_email

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _role_counts():
    """Đếm số người đang giữ mỗi chức vụ (chỉ tính thành viên còn hoạt động)."""
    counts = {}
    for member in User.query.filter(User.status == "approved", User.chuc_vu != SYSTEM_ADMIN_ROLE):
        if approved(member):
            counts[member.chuc_vu] = counts.get(member.chuc_vu, 0) + 1
    return counts


@admin_bp.route("/approve")
@login_required
@permission_required(Permission.APPROVE_MEMBERS)
def approve_list():
    """Trang danh sách đơn đăng ký đang chờ duyệt (cần quyền APPROVE_MEMBERS)."""
    pending_users = User.query.filter_by(status="pending").order_by(User.ngay_tao.asc()).all()
    return render_template("admin_approve.html", pending_users=pending_users)


@admin_bp.route("/approve/<int:user_id>", methods=["POST"])
@login_required
@permission_required(Permission.APPROVE_MEMBERS)
def approve_user(user_id):
    """Duyệt (accept) hoặc từ chối (reject) một đơn đăng ký; khi duyệt thì tạo MemberRecord, ghi kỳ
    tham gia, sau đó gửi email thông báo. Khoản thu "tất cả thành viên" tự áp dụng cho người mới.
    """
    user = db.get_or_404(User, user_id)
    if user.status != "pending":
        abort(400)
    action = request.form.get("action")  # 'accept' | 'reject'
    if action == "accept":
        user.status = "approved"
        ky_hien_tai = FundPeriod.query.filter_by(is_current=True).first()
        user.ky_tham_gia_id = ky_hien_tai.id if ky_hien_tai else None
        member_record(user)
        audit(current_user, "member_approve", user)
        db.session.commit()
        mailed = send_email(user.email, *render_system_email(
            "member_approved", ten=user.ho_ten, mssv=user.mssv))
        flash(f"Đã duyệt {user.ho_ten} thành thành viên CLB.", "success")
    elif action == "reject":
        user.status = "rejected"
        audit(current_user, "member_reject", user)
        db.session.commit()
        mailed = send_email(user.email, *render_system_email(
            "member_rejected", ten=user.ho_ten, mssv=user.mssv))
        flash(f"Đã từ chối {user.ho_ten}.", "success")
    else:
        abort(400)
    if not mailed:
        flash("Email thông báo chưa gửi được. Kiểm tra cấu hình SMTP và thông báo trực tiếp cho thành viên.", "error")
    return redirect(url_for("admin.approve_list"))


@admin_bp.route("/roles")
@login_required
@any_permission_required(Permission.ASSIGN_ROLES, Permission.HANDOFF_ROLE)
def role_list():
    """Trang vai trò & phân quyền: danh sách thành viên, nút gán chức vụ (chỉ với người thấp cấp
    hơn) và hộp nhường chức vụ.
    """
    members = (User.query.filter(User.status == "approved", User.chuc_vu != SYSTEM_ADMIN_ROLE)
               .order_by(User.ho_ten).all())
    members = [member for member in members if approved(member)]
    # Vai trò người đang xem được phép gán: thấp hơn cấp của mình; CN chỉ Quản trị hệ thống gán
    my_rank = role_rank(current_user.chuc_vu)
    assignable_roles = [name for name, spec in DEFAULT_ROLES.items()
                        if name != SYSTEM_ADMIN_ROLE and spec["rank"] > my_rank
                        and (name != "CN" or current_user.chuc_vu == SYSTEM_ADMIN_ROLE)]
    assignable_ids = {member.id for member in members
                      if assignable_roles and can_assign_role_to(current_user, member, assignable_roles[-1])}
    return render_template(
        "admin_roles.html", members=members, bans=Ban.query.all(), counts=_role_counts(),
        assignable_roles=assignable_roles, assignable_ids=assignable_ids,
        role_labels={name: spec["label"] for name, spec in DEFAULT_ROLES.items()},
    )


@admin_bp.route("/roles/<int:user_id>/assign", methods=["POST"])
@login_required
@fresh_login_required
@permission_required(Permission.ASSIGN_ROLES)
def assign_role(user_id):
    """Gán chức vụ mới cho một thành viên sau khi kiểm tra cấp bậc, giới hạn số lượng và quy tắc
    "không hạ BĐH cuối cùng" (cần fresh login + ASSIGN_ROLES).
    """
    target = db.get_or_404(User, user_id)
    new_role = request.form.get("chuc_vu")
    ban_id = request.form.get("ban_id", type=int)  # bắt buộc khi new_role == 'TB'

    counts = _role_counts()
    # Không tính trùng khi người được gán đang giữ đúng chức vụ đó
    if target.chuc_vu == new_role:
        counts[new_role] = max(0, counts.get(new_role, 0) - 1)

    ok, message = can_assign_role(current_user, target, new_role, counts)
    if not ok:
        flash(message, "error")
        return redirect(url_for("admin.role_list"))

    if new_role == "TB" and (not ban_id or not db.session.get(Ban, ban_id)):
        flash("Vui lòng chọn ban mà Trưởng ban này sẽ quản lý.", "error")
        return redirect(url_for("admin.role_list"))

    if not set_member_role_safely(target, new_role):
        flash("Không thể thay đổi vai trò quản trị viên cuối cùng hoặc dữ liệu vừa thay đổi.", "error")
        return redirect(url_for("admin.role_list"))
    # Bỏ quyền trưởng ban cũ khi chuyển sang chức vụ khác
    UserBan.query.filter_by(user_id=target.id, is_truong_ban=True).update({"is_truong_ban": False})
    target.chi_dinh_boi_id = current_user.id

    if new_role == "TB":
        link = UserBan.query.filter_by(user_id=target.id, ban_id=ban_id).first()
        if not link:
            link = UserBan(user_id=target.id, ban_id=ban_id)
            db.session.add(link)
        link.is_truong_ban = True

    audit(current_user, "role_assign", target, {"role": new_role, "ban_id": ban_id})
    db.session.commit()
    flash(f"Đã chỉ định {target.ho_ten} làm {DEFAULT_ROLES[new_role]['label']}.", "success")
    return redirect(url_for("admin.role_list"))


@admin_bp.route("/roles/nhuong-quyen", methods=["POST"])
@login_required
@fresh_login_required
@permission_required(Permission.HANDOFF_ROLE)
def nhuong_quyen():
    """Nhường chức vụ: người BĐH hiện tại giao chức vụ của mình cho một thành viên
    thường (target) và tự trở thành THANH_VIEN.
    """
    user_id = request.form.get("target_id", type=int)
    if user_id is None:
        abort(400)
    target = db.get_or_404(User, user_id)
    old_role = current_user.chuc_vu
    if target.id == current_user.id or not approved(target) or not current_user.outranks(target):
        abort(400)

    ban_id = request.form.get("ban_id", type=int)
    if old_role == "TB" and (not ban_id or ban_id not in current_user.quan_ly_ban_ids()):
        flash("Vui lòng chọn ban để nhường quyền Trưởng ban.", "error")
        return redirect(url_for("admin.role_list"))

    UserBan.query.filter_by(user_id=target.id, is_truong_ban=True).update({"is_truong_ban": False})

    if not set_member_role_safely(target, old_role):
        db.session.rollback()
        abort(409, "Vai trò của thành viên vừa thay đổi. Hãy tải lại trang.")
    target.chi_dinh_boi_id = current_user.id

    if old_role == "TB":
        link = UserBan.query.filter_by(user_id=target.id, ban_id=ban_id).first()
        if not link:
            link = UserBan(user_id=target.id, ban_id=ban_id)
            db.session.add(link)
        link.is_truong_ban = True

    if not set_member_role_safely(current_user, MEMBER_ROLE):
        db.session.rollback()
        abort(409, "Không thể hạ quyền quản trị viên cuối cùng.")
    UserBan.query.filter_by(user_id=current_user.id, is_truong_ban=True).update({"is_truong_ban": False})

    audit(current_user, "role_transfer", target, {"role": old_role})
    db.session.commit()
    label = DEFAULT_ROLES.get(old_role, {}).get("label", old_role)
    flash(f"Bạn đã nhường chức vụ {label} cho {target.ho_ten} và trở thành thành viên.", "success")
    return redirect(url_for("main.home"))


@admin_bp.route("/members/<int:user_id>/temp-password", methods=["POST"])
@login_required
@fresh_login_required
@permission_required(Permission.MANAGE_MEMBERS)
def issue_temp_password(user_id):
    """Cấp mật khẩu tạm khi thành viên quên mật khẩu mà email không dùng được.

    Mật khẩu tạm chỉ hiển thị MỘT lần trên trang kết quả (không đưa vào flash
    vì flash nằm trong cookie session). Thành viên bị bắt đổi mật khẩu ngay
    lần đăng nhập kế tiếp.
    """
    target = db.get_or_404(User, user_id)
    if not approved(target) or not can_issue_temp_password(current_user, target):
        abort(403)
    temporary = secrets.token_urlsafe(9)
    target.set_password(temporary)
    target.must_change_password = True
    audit(current_user, "member_temp_password", target)
    db.session.commit()
    response = render_template("admin_temp_password.html", member=target, temporary=temporary)
    return response, 200, {"Cache-Control": "no-store"}
