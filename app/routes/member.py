import io
from flask import Blueprint, render_template, request, send_file
from flask_login import login_required, current_user

from app.models import User, Ban
from app.permissions import approved_required
from app.services.access import directory_members, visible_members, can_view_all_members
from app.services.activities import (
    ACTIVE_LEVELS, FOLLOW_UP_LEVELS, LABELS, current_period, level_filter, levels_for,
)

member_bp = Blueprint("member", __name__)


def _parse_filters():
    """Đọc bộ lọc của trang Thành viên: q, ban_id, sort, status."""
    q = request.args.get("q", "").strip()
    ban_id = request.args.get("ban_id", type=int)
    sort_by = request.args.get("sort", "ho_ten")  # ho_ten | mssv
    status = request.args.get("status", "").strip()
    return q, ban_id, sort_by, status


def _filtered_members(base_query):
    """Áp bộ lọc và sắp xếp lên `base_query` (danh bạ hoặc phạm vi xem chi tiết)."""
    q, ban_id, sort_by, status = _parse_filters()

    members = base_query

    if q:
        like = f"%{q}%"
        members = members.filter(
            (User.ho_ten.ilike(like)) | (User.mssv.ilike(like))
        )
    if ban_id:
        members = members.join(User.ban_links).filter_by(ban_id=ban_id)
    if status in LABELS and can_view_all_members(current_user):
        members = members.filter(level_filter(status))

    if sort_by == "mssv":
        members = members.order_by(User.mssv.asc())
    else:
        members = members.order_by(User.ho_ten.asc())

    return members.all()


@member_bp.route("/member")
@login_required
@approved_required
def member_list():
    """Trang Thành viên: mọi thành viên thấy danh bạ CLB (thông tin cơ bản); người có
    MANAGE_MEMBERS thấy thêm liên hệ, tình trạng hoạt động và hồ sơ chi tiết.
    """
    members = _filtered_members(directory_members(current_user))
    bans = Ban.query.all()
    q, ban_id, sort_by, status = _parse_filters()
    visible = directory_members(current_user)
    if ban_id:
        visible = visible.join(User.ban_links).filter_by(ban_id=ban_id)
    total_members = visible.count()
    active_members = visible.filter(level_filter(ACTIVE_LEVELS)).count()
    follow_up_members = visible.filter(level_filter(FOLLOW_UP_LEVELS)).count()
    period = current_period()
    is_bdh = can_view_all_members(current_user)
    # Người có quyền quản lý thành viên xem đầy đủ; thành viên thường chỉ thấy chính mình
    return render_template(
        "member.html",
        members=members,
        bans=bans,
        is_bdh=is_bdh,
        activity_levels=levels_for(members, period) if is_bdh else {},
        activity_labels=LABELS,
        activity_period=period,
        selected_q=q,
        selected_ban_id=ban_id,
        selected_sort=sort_by,
        selected_status=status,
        total_members=total_members,
        active_members=active_members,
        follow_up_members=follow_up_members,
    )


@member_bp.route("/member/export")
@login_required
@approved_required
def export_excel():
    """Xuất danh sách thành viên (theo bộ lọc hiện tại) ra file Excel."""
    from openpyxl import Workbook

    members = _filtered_members(visible_members(current_user))
    wb = Workbook()
    ws = wb.active
    ws.title = "Danh sách thành viên"

    if can_view_all_members(current_user):
        headers = ["Họ tên", "MSSV", "Chức vụ", "Ban hoạt động", "SĐT", "Email", "Tình trạng hoạt động"]
    else:
        headers = ["Họ tên", "MSSV", "Chức vụ", "Ban hoạt động"]
    ws.append(headers)

    levels = levels_for(members) if can_view_all_members(current_user) else {}
    for m in members:
        ban_names = ", ".join(link.ban.ten_ban for link in m.ban_links)
        row = [m.ho_ten, m.mssv, m.chuc_vu, ban_names]
        if can_view_all_members(current_user):
            row += [m.sdt, m.email, LABELS[levels[m.id]]]
        from app.services.export_security import safe_excel_row
        ws.append(safe_excel_row(row))

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name="danh_sach_thanh_vien.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
