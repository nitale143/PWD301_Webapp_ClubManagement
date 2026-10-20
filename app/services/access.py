"""Chính sách phân quyền tập trung (Ch7 – RBAC).

Mọi route/service hỏi quyền qua các hàm ở đây thay vì so sánh tên chức vụ,
để đổi chính sách chỉ cần sửa bảng `role` (xem app.models.DEFAULT_ROLES).

Một người có quyền khi: tài khoản đã được duyệt (approved), chưa rời CLB/lưu
trữ, VÀ chức vụ của họ có bit quyền tương ứng.
"""
from app import db
from app.models import (
    DEFAULT_ROLES, SYSTEM_ADMIN_ROLE, MemberRecord, Permission, User, UserBan, role_rank,
)
from .common import DomainError


def approved(user):
    """Tài khoản đã được duyệt và vẫn đang là thành viên (không rời CLB/lưu trữ)."""
    if not user or getattr(user, "status", None) != "approved":
        return False
    record = db.session.get(MemberRecord, user.id)
    return record is None or (record.luu_tru_luc is None and record.tinh_trang != "left")


# Các chức vụ thuộc Ban điều hành (BĐH) = các role có quyền BOARD
ADMIN_ROLES = frozenset(name for name, spec in DEFAULT_ROLES.items()
                        if spec["permissions"] & Permission.BOARD)
MEMBER_ROLE = "THANH_VIEN"


def has_permission(user, perm):
    """Kiểm tra quyền chuẩn: approved + chức vụ có bit `perm`."""
    return bool(approved(user) and user.can(perm))


def is_admin(user):
    """Thuộc BĐH (có quyền BOARD)."""
    return has_permission(user, Permission.BOARD)


def is_board(user):
    """Bí danh của is_admin (thuộc BĐH)."""
    return is_admin(user)


def can_approve_members(user):
    """Có quyền xem danh sách chờ và duyệt đăng ký (APPROVE_MEMBERS)."""
    return has_permission(user, Permission.APPROVE_MEMBERS)


def can_manage_members(user):
    """Có quyền quản lý thành viên (MANAGE_MEMBERS)."""
    return has_permission(user, Permission.MANAGE_MEMBERS)


def can_manage_events(user):
    """Có quyền quản lý sự kiện & task (MANAGE_EVENTS)."""
    return has_permission(user, Permission.MANAGE_EVENTS)


def can_manage_funds(user):
    """Có quyền ghi/duyệt quỹ (MANAGE_FUNDS)."""
    return has_permission(user, Permission.MANAGE_FUNDS)


def is_treasurer(user):
    """Giữ tên cũ cho tương thích: người có quyền quản lý quỹ."""
    return can_manage_funds(user)


def can_view_all_members(user):
    """Xem đầy đủ hồ sơ/liên hệ của mọi thành viên."""
    return can_manage_members(user)


def can_manage_member(actor, target):
    """Sửa/lưu trữ/cộng điểm cho người có cấp thấp hơn (không áp dụng cho chính mình).

    Quản trị hệ thống luôn được bảo vệ khỏi thay đổi hồ sơ từ các route quản lý thành viên,
    kể cả khi dữ liệu bảng role bị cấu hình sai thứ hạng.
    """
    return bool(can_manage_members(actor) and target and actor.id != target.id
                and target.chuc_vu != SYSTEM_ADMIN_ROLE
                and role_rank(target.chuc_vu) > role_rank(actor.chuc_vu))


def can_issue_temp_password(actor, target):
    """Cấp mật khẩu tạm: cần quyền quản lý thành viên và cấp cao hơn người nhận
    (Trưởng ban không thể chiếm tài khoản Chủ nhiệm bằng cách đặt lại mật khẩu)."""
    return can_manage_member(actor, target)


def can_assign_role_to(actor, target, new_role):
    """Quy tắc phân vai trò theo cấp bậc (rank: số nhỏ = cấp cao).

    - Cần quyền ASSIGN_ROLES và không tự đổi vai trò của mình.
    - Chỉ đổi vai trò của người có cấp THẤP hơn mình.
    - Vai trò mới phải thấp hơn cấp của mình (PCN không gán PCN/CN).
    - Chủ nhiệm: chỉ Quản trị hệ thống gán được (lần đầu / khi CN cũ không còn);
      giữa các thành viên, CN đổi qua quy trình nhường quyền.
    - Không ai gán hoặc đổi vai trò Quản trị hệ thống qua giao diện.
    """
    from app.models import role_rank
    if not has_permission(actor, Permission.ASSIGN_ROLES) or not target or actor.id == target.id:
        return False
    if new_role not in DEFAULT_ROLES or new_role == SYSTEM_ADMIN_ROLE:
        return False
    if target.chuc_vu == SYSTEM_ADMIN_ROLE:
        return False
    if new_role == "CN" and actor.chuc_vu != SYSTEM_ADMIN_ROLE:
        return False
    my_rank = role_rank(actor.chuc_vu)
    return role_rank(target.chuc_vu) > my_rank and role_rank(new_role) > my_rank


def _active_admin_count():
    """Subquery đếm số thành viên BĐH còn hoạt động (dùng trong câu UPDATE có điều kiện)."""
    return (db.session.query(db.func.count(User.id))
            .outerjoin(MemberRecord, User.id == MemberRecord.user_id)
            .filter(User.status == "approved", User.chuc_vu.in_(ADMIN_ROLES),
                    db.or_(MemberRecord.user_id.is_(None), db.and_(
                        MemberRecord.luu_tru_luc.is_(None),
                        MemberRecord.tinh_trang != "left")))
            .scalar_subquery())


def _active_member_row(user_id):
    """Điều kiện SQL: thành viên chưa rời CLB / chưa lưu trữ."""
    active = (db.session.query(MemberRecord.user_id)
              .filter(MemberRecord.user_id == user_id,
                      MemberRecord.luu_tru_luc.is_(None),
                      MemberRecord.tinh_trang != "left").exists())
    missing = ~db.session.query(MemberRecord.user_id).filter(
        MemberRecord.user_id == user_id).exists()
    return db.or_(active, missing)


def set_member_role_safely(target, new_role):
    """Atomically update a role without removing the last active admin."""
    old_role = target.chuc_vu
    conditions = [User.id == target.id, User.status == "approved",
                  User.chuc_vu == old_role, _active_member_row(User.id)]
    if old_role in ADMIN_ROLES and new_role not in ADMIN_ROLES:
        conditions.append(_active_admin_count() > 1)
    result = db.session.execute(
        db.update(User).where(*conditions).values(chuc_vu=new_role)
    )
    if result.rowcount != 1:
        return False
    db.session.expire(target, ["chuc_vu"])
    return True


def set_member_left_safely(target, record, archived_at=None, *, actor=None):
    """Mark a member left while atomically preserving one active admin."""
    values = {"tinh_trang": "left"}
    if archived_at is not None:
        values["luu_tru_luc"] = archived_at
    conditions = [MemberRecord.user_id == target.id]
    if actor is not None:
        # Do not archive using a rank check made before a concurrent promotion
        # or after the acting manager lost their role/approval.
        conditions.append(db.session.query(User.id).filter(
            User.id == target.id, User.chuc_vu == target.chuc_vu).exists())
        conditions.append(db.session.query(User.id).filter(
            User.id == actor.id, User.chuc_vu == actor.chuc_vu,
            User.status == "approved", _active_member_row(User.id)).exists())
    active_admin_in_database = db.session.query(User.id).filter(
        User.id == target.id, User.status == "approved", User.chuc_vu.in_(ADMIN_ROLES),
        _active_member_row(User.id)).exists()
    conditions.append(db.or_(~active_admin_in_database, _active_admin_count() > 1))
    result = db.session.execute(
        db.update(MemberRecord).where(*conditions).values(**values)
    )
    if result.rowcount != 1:
        return False
    record.tinh_trang = "left"
    if archived_at is not None:
        record.luu_tru_luc = archived_at
    return True


def can_view_member(actor, target):
    """Xem hồ sơ: chính mình, hoặc người có quyền quản lý thành viên."""
    return bool(actor and target and (actor.id == target.id or can_view_all_members(actor)))


def require(condition, message="Bạn không có quyền thực hiện thao tác này."):
    """Ném DomainError 403 nếu điều kiện sai – dùng trong service để chặn thao tác không đủ quyền."""
    if not condition:
        raise DomainError(message, 403)


def require_current_approval(actor):
    """Re-read account and membership after a potentially slow external call."""
    db.session.refresh(actor)
    record = db.session.get(MemberRecord, actor.id, populate_existing=True)
    require(actor.status == "approved" and (record is None or (
        record.luu_tru_luc is None and record.tinh_trang != "left")),
        "Tài khoản không còn quyền sử dụng AI.")
    # Refresh relationship rows too: refreshing User alone can retain cached ban roles.
    UserBan.query.filter_by(user_id=actor.id).populate_existing().all()
    db.session.expire(actor, ["ban_links"])


def actor_scope_stamp(actor):
    """"Dấu" phạm vi quyền hiện tại (chức vụ) để phát hiện quyền bị đổi giữa chừng khi AI xử lý
    lâu.
    """
    return actor.chuc_vu


def require_unchanged_scope(actor, stamp):
    """Chặn nếu chức vụ đã thay đổi kể từ khi bắt đầu xử lý yêu cầu."""
    require(stamp == actor_scope_stamp(actor),
            "Quyền hoặc ban phụ trách đã thay đổi trong lúc xử lý. Hãy gửi yêu cầu mới.")


def active_members():
    """Mọi thành viên đã duyệt và chưa bị lưu trữ (dùng nội bộ cho quỹ/sự kiện).

    Không gồm tài khoản Quản trị hệ thống (không phải thành viên CLB).
    """
    return (User.query.filter(User.status == "approved", User.chuc_vu != SYSTEM_ADMIN_ROLE)
            .outerjoin(MemberRecord, User.id == MemberRecord.user_id)
            .filter(MemberRecord.luu_tru_luc.is_(None)))


def visible_members(actor):
    """Thành viên mà `actor` được xem hồ sơ: tất cả nếu có MANAGE_MEMBERS, ngược lại chỉ bản thân."""
    query = active_members()
    if can_view_all_members(actor):
        return query
    return query.filter(User.id == getattr(actor, "id", -1))


def directory_members(actor):
    """Danh bạ CLB: mọi thành viên đã duyệt đều thấy danh sách thành viên đang hoạt động.

    Chỉ dùng cho thông tin CƠ BẢN (tên, MSSV, ban, vai trò). Liên hệ, tình trạng hoạt
    động và hồ sơ chi tiết vẫn chỉ dành cho người có MANAGE_MEMBERS (visible_members).
    """
    if not approved(actor):
        return active_members().filter(db.false())
    return active_members()


def board_members(actor):
    """Phạm vi tra cứu tên/MSSV cho AI và nháp email: mọi thành viên nếu thuộc BĐH,
    ngược lại chỉ bản thân. Không bao gồm quyền xem thông tin liên hệ."""
    return scoped_members(actor, Permission.BOARD)


def scoped_members(actor, perm):
    """Thành viên trong phạm vi một nghiệp vụ: tất cả nếu có quyền `perm`, ngược lại chỉ bản thân.

    Ví dụ thủ quỹ (MANAGE_FUNDS) xem tình trạng đóng quỹ của mọi người dù không
    có quyền quản lý hồ sơ thành viên.
    """
    query = active_members()
    if has_permission(actor, perm):
        return query
    return query.filter(User.id == getattr(actor, "id", -1))
