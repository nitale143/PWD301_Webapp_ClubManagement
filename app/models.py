"""Các model SQLAlchemy của webapp CLB (Ch5 – Database & ORM, Ch7 – RBAC).

Quy ước đặt tên: tên bảng/cột giữ tiếng Việt không dấu như database cũ để
không phải đổi dữ liệu đã có. Thời gian lưu dạng UTC naive (xem app.timeutil).
Schema thay đổi qua Flask-Migrate: sửa model → `flask db migrate -m "..."`
→ kiểm tra file trong migrations/versions → `flask db upgrade`.
"""
from flask_login import AnonymousUserMixin, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app import db, login_manager
from app.timeutil import utcnow


# ---------------------------------------------------------------------------
# Phân quyền RBAC (Ch7): User → Role → Permission (bit flag)
# ---------------------------------------------------------------------------
class Permission:
    """Các quyền dạng bit flag. Một Role lưu tổng (OR) các bit nó có.

    Kiểm tra quyền: ``role.permissions & Permission.X == Permission.X``.
    """

    APPROVE_MEMBERS = 1    # Xem danh sách chờ duyệt và duyệt/từ chối đăng ký mới
    MANAGE_MEMBERS = 2     # Xem đầy đủ hồ sơ, sửa, lưu trữ, import, cộng/trừ điểm thành viên
    MANAGE_EVENTS = 4      # Tạo/sửa sự kiện, giao và duyệt task, điểm danh
    MANAGE_FUNDS = 8       # Ghi thu chi, mở kỳ quỹ, tạo khoản thu, xác nhận thanh toán
    ASSIGN_ROLES = 16      # Phân vai trò cho thành viên khác (giới hạn theo cấp bậc)
    HANDOFF_ROLE = 32      # Nhường chức vụ của chính mình cho một thành viên
    BOARD = 64             # Thuộc Ban điều hành: báo cáo tổng hợp, nháp email, khu vực quản trị
    MANAGE_TEMPLATES = 128 # Quản lý kho mẫu email dùng chung

    LABELS = {
        APPROVE_MEMBERS: "Xem/duyệt đăng ký thành viên",
        MANAGE_MEMBERS: "Quản lý thành viên",
        MANAGE_EVENTS: "Sự kiện & task",
        MANAGE_FUNDS: "Quỹ (ghi/duyệt)",
        ASSIGN_ROLES: "Phân vai trò",
        HANDOFF_ROLE: "Nhường chức vụ của bản thân",
        BOARD: "Khu vực Ban điều hành",
        MANAGE_TEMPLATES: "Quản lý kho mẫu email",
    }


# Bảng quyền mặc định theo chức vụ. Thứ tự rank: số nhỏ = cấp cao hơn.
# Muốn đổi chính sách: sửa bảng này rồi chạy `flask --app run.py sync-roles`.
# Tài khoản quản trị hệ thống: tạo đầu tiên bằng `flask create-admin`, không phải thành viên CLB,
# dùng để duyệt/gán những thành viên và BĐH đầu tiên (kể cả Chủ nhiệm).
SYSTEM_ADMIN_ROLE = "ADMIN"

DEFAULT_ROLES = {
    SYSTEM_ADMIN_ROLE: {"label": "Quản trị hệ thống", "rank": -1, "permissions": (
        Permission.APPROVE_MEMBERS | Permission.MANAGE_MEMBERS | Permission.MANAGE_EVENTS
        | Permission.MANAGE_FUNDS | Permission.ASSIGN_ROLES | Permission.BOARD
        | Permission.MANAGE_TEMPLATES)},
    "CN": {"label": "Chủ nhiệm", "rank": 0, "permissions": (
        Permission.APPROVE_MEMBERS | Permission.MANAGE_MEMBERS | Permission.MANAGE_EVENTS
        | Permission.MANAGE_FUNDS | Permission.ASSIGN_ROLES | Permission.HANDOFF_ROLE
        | Permission.BOARD | Permission.MANAGE_TEMPLATES)},
    "PCN": {"label": "Phó chủ nhiệm", "rank": 1, "permissions": (
        Permission.APPROVE_MEMBERS | Permission.MANAGE_MEMBERS | Permission.MANAGE_EVENTS
        | Permission.MANAGE_FUNDS | Permission.ASSIGN_ROLES | Permission.HANDOFF_ROLE
        | Permission.BOARD | Permission.MANAGE_TEMPLATES)},
    "TB": {"label": "Trưởng ban", "rank": 2, "permissions": (
        Permission.MANAGE_MEMBERS | Permission.MANAGE_EVENTS | Permission.HANDOFF_ROLE
        | Permission.BOARD)},
    "THUKY_THUQUY": {"label": "Thư ký / Thủ quỹ", "rank": 2, "permissions": (
        Permission.MANAGE_EVENTS | Permission.MANAGE_FUNDS | Permission.HANDOFF_ROLE
        | Permission.BOARD)},
    "THANH_VIEN": {"label": "Thành viên", "rank": 9, "permissions": 0, "default": True},
}


class Role(db.Model):
    """Một chức vụ trong CLB và tập quyền của nó (Ch7 – slide 43-48)."""

    __tablename__ = "role"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)   # khớp User.chuc_vu
    label = db.Column(db.String(64), nullable=False)
    rank = db.Column(db.Integer, nullable=False, default=9)
    default = db.Column(db.Boolean, nullable=False, default=False, index=True)
    permissions = db.Column(db.Integer, nullable=False, default=0)

    def has_permission(self, perm):
        """True nếu role có bit quyền `perm`."""
        return (self.permissions or 0) & perm == perm

    def add_permission(self, perm):
        """Thêm bit quyền vào role (không đổi nếu đã có)."""
        if not self.has_permission(perm):
            self.permissions = (self.permissions or 0) + perm

    def remove_permission(self, perm):
        """Bỏ bit quyền khỏi role (không đổi nếu chưa có)."""
        if self.has_permission(perm):
            self.permissions -= perm

    def reset_permissions(self):
        """Xóa mọi quyền của role."""
        self.permissions = 0

    @staticmethod
    def insert_roles(overwrite=False):
        """Tạo các Role mặc định nếu chưa có. overwrite=True ghi đè quyền theo DEFAULT_ROLES."""
        for name, spec in DEFAULT_ROLES.items():
            role = Role.query.filter_by(name=name).first()
            if role is None:
                role = Role(name=name, label=spec["label"], rank=spec["rank"],
                            permissions=spec["permissions"], default=spec.get("default", False))
                db.session.add(role)
            elif overwrite:
                role.label, role.rank = spec["label"], spec["rank"]
                role.permissions = spec["permissions"]
                role.default = spec.get("default", False)
        db.session.commit()

    def __repr__(self):
        """Chuỗi mô tả role khi debug."""
        return f"<Role {self.name} perms={self.permissions}>"


def role_permissions(role_name):
    """Quyền của một chức vụ: đọc bảng role; nếu chưa seed thì dùng DEFAULT_ROLES."""
    role = Role.query.filter_by(name=role_name).first() if role_name else None
    if role is not None:
        return role.permissions or 0
    return DEFAULT_ROLES.get(role_name, {}).get("permissions", 0)


def role_rank(role_name):
    """Cấp bậc của một chức vụ (số nhỏ = cấp cao): đọc bảng role, chưa seed thì dùng DEFAULT_ROLES."""
    role = Role.query.filter_by(name=role_name).first() if role_name else None
    if role is not None:
        return role.rank
    return DEFAULT_ROLES.get(role_name, {}).get("rank", 9)


# ---------------------------------------------------------------------------
# Bảng trung gian: thành viên <-> ban hoạt động (nhiều-nhiều)
# ---------------------------------------------------------------------------
class UserBan(db.Model):
    """Bảng trung gian nhiều-nhiều User ↔ Ban; `is_truong_ban` đánh dấu Trưởng ban của ban đó."""
    __tablename__ = "user_ban"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    ban_id = db.Column(db.Integer, db.ForeignKey("ban.id"), nullable=False)
    # True nếu người này là Trưởng ban của ban này
    is_truong_ban = db.Column(db.Boolean, default=False)

    user = db.relationship("User", back_populates="ban_links")
    ban = db.relationship("Ban", back_populates="user_links")


class Ban(db.Model):
    """Ban hoạt động: Kỹ thuật, Nhân sự, Truyền thông."""
    __tablename__ = "ban"
    id = db.Column(db.Integer, primary_key=True)
    ten_ban = db.Column(db.String(50), unique=True, nullable=False)

    user_links = db.relationship("UserBan", back_populates="ban")


class User(UserMixin, db.Model):
    """Tài khoản thành viên CLB: thông tin cá nhân, trạng thái duyệt, chức vụ (khớp Role.name), xác
    thực (hash mật khẩu, xác minh email, bắt đổi mật khẩu).
    """
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    mssv = db.Column(db.String(20), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)

    ho_ten = db.Column(db.String(100), nullable=False)
    ngay_sinh = db.Column(db.Date, nullable=False)
    sdt = db.Column(db.String(15), nullable=False)
    email = db.Column(db.String(120), nullable=False, unique=True, index=True)
    facebook_link = db.Column(db.String(255))
    avatar_url = db.Column(db.String(255), default="/static/img/default_avatar.svg")

    # pending | approved | rejected
    status = db.Column(db.String(20), default="pending", nullable=False)
    # Tên Role (CN | PCN | TB | THUKY_THUQUY | THANH_VIEN) – xem bảng `role`
    chuc_vu = db.Column(db.String(20), default="THANH_VIEN", nullable=False)
    # Người đã chỉ định chức vụ này (phục vụ cơ chế nhường quyền)
    chi_dinh_boi_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    ngay_tao = db.Column(db.DateTime, default=utcnow)

    # Kỳ (FundPeriod) mà thành viên được BĐH duyệt vào. Kỳ này "Chưa xếp loại" hoạt động;
    # từ kỳ KẾ TIẾP mới tính (app/services/activities.py).
    ky_tham_gia_id = db.Column(db.Integer, db.ForeignKey("fund_period.id"), nullable=True)

    # --- Xác thực (Ch6) ---
    email_confirmed = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    email_confirmed_at = db.Column(db.DateTime)
    # Bắt đổi mật khẩu ở lần đăng nhập kế tiếp (tài khoản import / mật khẩu tạm)
    must_change_password = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    password_changed_at = db.Column(db.DateTime)

    ban_links = db.relationship(
        "UserBan", back_populates="user", foreign_keys=[UserBan.user_id]
    )
    role = db.relationship("Role", primaryjoin="foreign(User.chuc_vu) == Role.name",
                           viewonly=True, lazy="select")

    # ---- mật khẩu (Ch6: chỉ lưu hash, không bao giờ lưu plaintext) ----
    @property
    def password(self):
        """Không cho đọc mật khẩu (chỉ lưu hash)."""
        raise AttributeError("Mật khẩu không phải thuộc tính đọc được.")

    @password.setter
    def password(self, raw_password):
        """Gán mật khẩu mới: tự băm và lưu vào password_hash."""
        self.set_password(raw_password)

    def set_password(self, raw_password):
        """Băm mật khẩu bằng Werkzeug (scrypt) và ghi lại thời điểm đổi mật khẩu."""
        self.password_hash = generate_password_hash(raw_password)
        self.password_changed_at = utcnow()

    def check_password(self, raw_password):
        """So sánh mật khẩu nhập vào với hash đã lưu."""
        return check_password_hash(self.password_hash, raw_password)

    # ---- định danh phiên (Flask-Login) ----
    @property
    def session_marker(self):
        """Đổi mỗi khi đổi mật khẩu → mọi phiên/cookie ghi nhớ cũ tự mất hiệu lực."""
        changed = self.password_changed_at
        return int(changed.timestamp() * 1_000_000) if changed else 0

    def get_id(self):
        """Định danh lưu trong session/cookie của Flask-Login: "<id>:<session_marker>"."""
        return f"{self.id}:{self.session_marker}"

    # ---- token có chữ ký (itsdangerous) cho xác minh email / đặt lại mật khẩu ----
    def generate_token(self, purpose):
        """Token ký bằng SECRET_KEY. purpose: 'confirm' hoặc 'reset'.

        Token reset gắn với hash mật khẩu hiện tại nên chỉ dùng được một lần:
        đổi mật khẩu xong thì token cũ tự mất hiệu lực.
        """
        from app.tokens import dumps
        payload = {"uid": self.id}
        if purpose == "reset":
            payload["ph"] = self.password_hash[-16:]
        if purpose == "confirm":
            payload["email"] = self.email
        return dumps(payload, purpose)

    @staticmethod
    def from_token(token, purpose, max_age):
        """Trả về User nếu token hợp lệ, còn hạn và đúng mục đích; ngược lại None."""
        from app.tokens import loads
        data = loads(token, purpose, max_age)
        if not isinstance(data, dict) or not isinstance(data.get("uid"), int):
            return None
        user = db.session.get(User, data["uid"])
        if user is None:
            return None
        if purpose == "reset" and data.get("ph") != user.password_hash[-16:]:
            return None
        if purpose == "confirm" and data.get("email") != user.email:
            return None
        return user

    def confirm_email(self):
        """Đánh dấu email đã xác minh và ghi thời điểm xác minh."""
        if not self.email_confirmed:
            self.email_confirmed = True
            self.email_confirmed_at = utcnow()

    # ---- phân quyền (Ch7) ----
    @property
    def permissions(self):
        """Tổng bit quyền của chức vụ hiện tại."""
        return role_permissions(self.chuc_vu)

    def can(self, perm):
        """True nếu chức vụ hiện tại có quyền `perm` (chưa xét trạng thái duyệt)."""
        return self.permissions & perm == perm

    def is_administrator(self):
        """True nếu chức vụ có quyền phân vai trò (CN/PCN theo bảng mặc định)."""
        return self.can(Permission.ASSIGN_ROLES)

    @property
    def rank(self):
        """Cấp bậc của chức vụ hiện tại (số nhỏ = cấp cao)."""
        return role_rank(self.chuc_vu)

    @property
    def role_label(self):
        """Tên hiển thị tiếng Việt của chức vụ."""
        return DEFAULT_ROLES.get(self.chuc_vu, {}).get("label", self.chuc_vu)

    def outranks(self, other_user):
        """True khi người BĐH (quyền BOARD) nhường chức vụ cho một thành viên thường."""
        return self.is_bdh() and other_user.chuc_vu == "THANH_VIEN"

    def is_bdh(self):
        """Thuộc Ban điều hành (có quyền BOARD)."""
        return self.can(Permission.BOARD)

    @property
    def is_system_admin(self):
        """Tài khoản quản trị hệ thống (không phải thành viên CLB)."""
        return self.chuc_vu == SYSTEM_ADMIN_ROLE

    def quan_ly_ban_ids(self):
        """Các ban_id mà Trưởng ban này quản lý (rỗng nếu không phải TB)."""
        if self.chuc_vu != "TB":
            return []
        return [link.ban_id for link in self.ban_links if link.is_truong_ban]

    def __repr__(self):
        """Chuỗi mô tả user khi debug."""
        return f"<User {self.mssv} - {self.ho_ten}>"


class AnonymousUser(AnonymousUserMixin):
    """Người chưa đăng nhập: không có quyền nào (để template gọi can() an toàn)."""

    chuc_vu = None
    status = None

    def can(self, perm):
        """Người chưa đăng nhập không có quyền nào."""
        return False

    def is_bdh(self):
        """Người chưa đăng nhập không thuộc BĐH."""
        return False

    def is_administrator(self):
        """Người chưa đăng nhập không phải quản trị."""
        return False

    is_system_admin = False


login_manager.anonymous_user = AnonymousUser


@login_manager.user_loader
def load_user(user_id):
    """Nạp user từ session/cookie. Định dạng id: "<id>:<session_marker>".

    Nếu marker không khớp (mật khẩu đã đổi sau khi phiên được tạo) → coi như
    chưa đăng nhập. Dạng cũ chỉ có "<id>" chỉ được chấp nhận khi TESTING.
    """
    from flask import current_app
    raw_id, _, marker = str(user_id).partition(":")
    try:
        user = db.session.get(User, int(raw_id))
    except (TypeError, ValueError):
        return None
    if user is None:
        return None
    if not marker:
        return user if current_app.config.get("TESTING") else None
    return user if marker == str(user.session_marker) else None


# ---------------------------------------------------------------------------
# Sự kiện / Task
# ---------------------------------------------------------------------------
class Event(db.Model):
    """Sự kiện của CLB; `tasks` là các task con. Trạng thái: sap_dien_ra | dang_dien_ra |
    da_ket_thuc | cancelled – chỉ được đổi qua app/services/event_status.py (BĐH chuyển tay hoặc
    hệ thống tự chuyển theo giờ).
    """
    __tablename__ = "event"
    id = db.Column(db.Integer, primary_key=True)
    ten_su_kien = db.Column(db.String(150), nullable=False)
    ma_su_kien = db.Column(db.String(30), unique=True, nullable=False)
    thoi_gian_bat_dau = db.Column(db.DateTime, nullable=False)
    thoi_gian_ket_thuc = db.Column(db.DateTime, nullable=False)
    # sap_dien_ra | dang_dien_ra | da_ket_thuc | cancelled
    trang_thai = db.Column(db.String(20), nullable=False, default="sap_dien_ra")
    # Lần gần nhất BĐH đổi trạng thái bằng tay (giờ CLB). Mở lại sự kiện thì hệ thống chỉ tự
    # kết thúc sau EVENT_AUTO_CLOSE_HOURS tính từ lúc mở lại. NULL = chưa đổi tay lần nào.
    trang_thai_doi_tay_luc = db.Column(db.DateTime)
    tao_boi_id = db.Column(db.Integer, db.ForeignKey("user.id"))

    tasks = db.relationship("Task", backref="event", cascade="all, delete-orphan")

    __table_args__ = (
        db.CheckConstraint("trang_thai IN ('sap_dien_ra','dang_dien_ra','da_ket_thuc','cancelled')",
                           name="ck_event_status"),
    )


class Task(db.Model):
    """Đầu việc thuộc một sự kiện, giao cho một người phụ trách (assignee) thuộc một ban. Trạng
    thái: dang_lam → cho_duyet → lam_lai/hoan_thanh, hoặc huy.
    """
    __tablename__ = "task"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    ten_task = db.Column(db.String(150), nullable=False)
    mo_ta = db.Column(db.Text)
    deadline = db.Column(db.DateTime, nullable=False)

    # dang_lam | cho_duyet | lam_lai | hoan_thanh | huy
    trang_thai = db.Column(db.String(20), default="dang_lam")

    # Ban mà task này thuộc về (để tính tình trạng hoạt động theo ban)
    ban_id = db.Column(db.Integer, db.ForeignKey("ban.id"), nullable=True)

    # Người phụ trách được chỉ định NGAY khi tạo task (không có cơ chế xung phong)
    assignee_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    noi_dung_nop = db.Column(db.Text)          # sản phẩm thành viên nộp
    lan_nop_gan_nhat = db.Column(db.DateTime)  # lần nộp gần nhất
    nhan_xet_bdh = db.Column(db.Text)          # nhận xét khi yêu cầu "làm lại"

    assignee = db.relationship("User", foreign_keys=[assignee_id])
    ban = db.relationship("Ban")


# ---------------------------------------------------------------------------
# Quỹ CLB (kỳ quản lý theo lịch học: Spring / Summer / Fall)
# ---------------------------------------------------------------------------
class FundPeriod(db.Model):
    """Kỳ quỹ / kỳ hoạt động (Spring, Summer, Fall…). Chỉ một kỳ `is_current`; chứa sổ thu chi và
    trạng thái đóng quỹ theo kỳ.
    """
    __tablename__ = "fund_period"
    id = db.Column(db.Integer, primary_key=True)
    ten_ky = db.Column(db.String(50), nullable=False)  # vd: "Spring 2027"
    ngay_bat_dau = db.Column(db.Date, nullable=False)
    ngay_ket_thuc = db.Column(db.Date, nullable=True)
    is_current = db.Column(db.Boolean, default=False)
    # Đã nhắc nhập kỳ kế tiếp chưa (tránh nhắc lặp lại mỗi ngày)
    da_nhac_ky_moi = db.Column(db.Boolean, default=False)

    transactions = db.relationship(
        "FundTransaction", backref="period", cascade="all, delete-orphan"
    )
    dues = db.relationship("FundDue", backref="period", cascade="all, delete-orphan")


class FundTransaction(db.Model):
    """Một dòng trong sổ thu chi của kỳ: thu (số dương) hoặc chi (số âm)."""
    __tablename__ = "fund_transaction"
    id = db.Column(db.Integer, primary_key=True)
    period_id = db.Column(db.Integer, db.ForeignKey("fund_period.id"), nullable=False)
    danh_muc = db.Column(db.String(100), nullable=False)
    noi_dung = db.Column(db.String(255))
    ngay = db.Column(db.Date, nullable=False)
    so_tien = db.Column(db.Numeric(14, 2), nullable=False)  # thu: +, chi: -
    tao_boi_id = db.Column(db.Integer, db.ForeignKey("user.id"))


class FundDue(db.Model):
    """Trạng thái đóng quỹ của từng thành viên trong 1 kỳ (sổ quỹ theo kỳ)."""
    __tablename__ = "fund_due"
    id = db.Column(db.Integer, primary_key=True)
    period_id = db.Column(db.Integer, db.ForeignKey("fund_period.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    da_dong = db.Column(db.Boolean, default=False)
    ngay_dong = db.Column(db.Date, nullable=True)

    user = db.relationship("User")


# ---------------------------------------------------------------------------
# Tình trạng hoạt động: theo (thành viên, kỳ), tính lại khi sự kiện kết thúc
# (xem app/services/activities.py).
# ---------------------------------------------------------------------------
class MemberActivity(db.Model):
    """Tình trạng hoạt động của một thành viên trong một kỳ (tính bởi app/services/activities.py).

    Điểm = 50% tỉ lệ task hoàn thành + 50% tỉ lệ có mặt, chỉ trên sự kiện đã kết thúc trong kỳ.
    """
    __tablename__ = "member_activity"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    period_id = db.Column(db.Integer, db.ForeignKey("fund_period.id"), nullable=False)

    tong_task = db.Column(db.Integer, nullable=False, default=0)        # không tính task 'huy'
    task_hoan_thanh = db.Column(db.Integer, nullable=False, default=0)
    tong_su_kien = db.Column(db.Integer, nullable=False, default=0)     # không tính vắng có phép
    su_kien_co_mat = db.Column(db.Integer, nullable=False, default=0)   # đúng giờ + đi muộn
    diem = db.Column(db.Float)                                          # 0–100; NULL = chưa có dữ liệu
    # tich_cuc | hoat_dong | it_hoat_dong | khong_hoat_dong | chua_xep_loai
    trang_thai = db.Column(db.String(20), nullable=False, default="chua_xep_loai")
    cap_nhat_luc = db.Column(db.DateTime, nullable=False, default=utcnow)

    user = db.relationship("User")
    period = db.relationship("FundPeriod")

    __table_args__ = (
        db.UniqueConstraint("user_id", "period_id", name="uq_activity_user_period"),
    )


# ---------------------------------------------------------------------------
# Lịch sử chat AI
# ---------------------------------------------------------------------------
class ChatMessage(db.Model):
    """Một tin nhắn trong lịch sử chat AI (role 'user' hoặc 'ai'), thuộc một cuộc trò chuyện."""
    __tablename__ = "chat_message"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    role = db.Column(db.String(10), nullable=False)  # 'user' | 'ai'
    content = db.Column(db.Text, nullable=False)
    thoi_gian = db.Column(db.DateTime, default=utcnow)
    conversation_id = db.Column(db.Integer, db.ForeignKey("chat_conversation.id"), nullable=True)

    __table_args__ = (
        db.Index("ix_chat_message_conversation_id_id", "conversation_id", "id"),
    )


class ChatConversation(db.Model):
    """A bounded, user-owned tab in the AI chat history."""

    __tablename__ = "chat_conversation"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(100), nullable=False, default="Cuộc trò chuyện mới",
                      server_default="Cuộc trò chuyện mới")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow,
                            onupdate=utcnow)
    archived = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    legacy_marker = db.Column(db.String(16), nullable=True)

    __table_args__ = (
        db.UniqueConstraint("user_id", "legacy_marker", name="uq_chat_conversation_legacy"),
        db.Index("ix_chat_conversation_owner_archived_updated",
                 "user_id", "archived", "updated_at", "id"),
    )


class Notification(db.Model):
    """Thông báo hiện ở chuông trên thanh trên cùng (nhắc deadline, nhắc kỳ mới…)."""
    __tablename__ = "notification"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    noi_dung = db.Column(db.String(255), nullable=False)
    da_doc = db.Column(db.Boolean, default=False)
    thoi_gian = db.Column(db.DateTime, default=utcnow)


# Nghiệp vụ mở rộng: hồ sơ thành viên, khoản thu, sự kiện chi tiết, AI…
class MemberRecord(db.Model):
    """Hồ sơ thành viên mở rộng: ngày gia nhập, tình trạng (active/paused/left), ghi chú, thời điểm
    lưu trữ.
    """
    __tablename__ = "member_record"
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    ngay_gia_nhap = db.Column(db.Date, nullable=False, default=lambda: utcnow().date())
    tinh_trang = db.Column(db.String(20), nullable=False, default="active")
    ghi_chu = db.Column(db.Text, nullable=False, default="")
    luu_tru_luc = db.Column(db.DateTime)
    user = db.relationship("User", backref=db.backref("member_record", uselist=False))
    __table_args__ = (
        db.CheckConstraint("tinh_trang IN ('active','paused','left')", name="ck_member_status"),
    )


class FundCollection(db.Model):
    """Khoản thu (thu theo tháng/kỳ/năm/sự kiện/tự nguyện…), có số tiền, hạn đóng và trạng thái
    draft/open/closed/cancelled.
    """
    __tablename__ = "fund_collection"
    id = db.Column(db.Integer, primary_key=True)
    ten_khoan_thu = db.Column(db.String(180), nullable=False)
    loai = db.Column(db.String(20), nullable=False)
    so_tien = db.Column(db.Numeric(14, 2), nullable=False)
    ngay_bat_dau = db.Column(db.Date, nullable=False)
    han_dong = db.Column(db.Date, nullable=False, index=True)
    tat_ca_thanh_vien = db.Column(db.Boolean, nullable=False, default=True)
    mo_ta = db.Column(db.Text, nullable=False, default="")
    trang_thai = db.Column(db.String(12), nullable=False, default="draft", index=True)
    tao_boi_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    tao_luc = db.Column(db.DateTime, nullable=False, default=utcnow)
    __table_args__ = (
        db.CheckConstraint("so_tien >= 0", name="ck_fund_collection_amount"),
        db.CheckConstraint("han_dong >= ngay_bat_dau", name="ck_fund_collection_dates"),
        db.CheckConstraint("loai IN ('monthly','semester','yearly','event','voluntary','other')", name="ck_fund_collection_type"),
        db.CheckConstraint("trang_thai IN ('draft','open','closed','cancelled')", name="ck_fund_collection_status"),
        db.Index("ix_fund_collection_status_due", "trang_thai", "han_dong"),
    )


class FundTargetBan(db.Model):
    """Ban áp dụng của một khoản thu (khi khoản thu không áp dụng cho tất cả thành viên)."""
    __tablename__ = "fund_target_ban"
    fund_id = db.Column(db.Integer, db.ForeignKey("fund_collection.id"), primary_key=True)
    ban_id = db.Column(db.Integer, db.ForeignKey("ban.id"), primary_key=True)


class FundAdjustment(db.Model):
    """Miễn (exempt) hoặc giảm (reduce) khoản thu cho một thành viên, bắt buộc có lý do."""
    __tablename__ = "fund_adjustment"
    id = db.Column(db.Integer, primary_key=True)
    fund_id = db.Column(db.Integer, db.ForeignKey("fund_collection.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    loai = db.Column(db.String(10), nullable=False)
    so_tien = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    ly_do = db.Column(db.Text, nullable=False)
    tao_boi_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    tao_luc = db.Column(db.DateTime, nullable=False, default=utcnow)
    __table_args__ = (
        db.UniqueConstraint("fund_id", "user_id", name="uq_fund_adjustment_member"),
        db.CheckConstraint("loai IN ('exempt','reduce')", name="ck_fund_adjustment_type"),
        db.CheckConstraint("so_tien >= 0", name="ck_fund_adjustment_amount"),
        db.CheckConstraint("length(trim(ly_do)) > 0", name="ck_fund_adjustment_reason"),
    )


class FundPayment(db.Model):
    """Một lần đóng tiền cho khoản thu; chờ người quản lý quỹ xác nhận (pending →
    confirmed/rejected).
    """
    __tablename__ = "fund_payment"
    id = db.Column(db.Integer, primary_key=True)
    fund_id = db.Column(db.Integer, db.ForeignKey("fund_collection.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    so_tien = db.Column(db.Numeric(14, 2), nullable=False)
    ngay_dong = db.Column(db.DateTime, nullable=False, default=utcnow)
    phuong_thuc = db.Column(db.String(20), nullable=False)
    ma_giao_dich = db.Column(db.String(100), nullable=False, default="")
    trang_thai = db.Column(db.String(12), nullable=False, default="pending", index=True)
    xac_nhan_boi_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    xac_nhan_luc = db.Column(db.DateTime)
    ghi_chu = db.Column(db.Text, nullable=False, default="")
    __table_args__ = (
        db.CheckConstraint("so_tien > 0", name="ck_fund_payment_amount"),
        db.CheckConstraint("trang_thai IN ('pending','confirmed','rejected')", name="ck_fund_payment_status"),
        db.CheckConstraint("trang_thai != 'confirmed' OR xac_nhan_boi_id IS NOT NULL", name="ck_fund_payment_reviewer"),
        db.Index("ix_fund_payment_fund_member_status", "fund_id", "user_id", "trang_thai"),
    )


class PaymentEvidence(db.Model):
    """Ảnh minh chứng thanh toán, lưu trong instance/payment_evidence với tên ngẫu nhiên."""
    __tablename__ = "payment_evidence"
    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.Integer, db.ForeignKey("fund_payment.id"), nullable=False)
    duong_dan = db.Column(db.String(255), nullable=False)
    tai_len_luc = db.Column(db.DateTime, nullable=False, default=utcnow)


class EventDetail(db.Model):
    """Thông tin chi tiết của sự kiện: loại, nội dung, địa điểm, hạn đăng ký, người phụ
    trách (cột so_nguoi_toi_da giữ lại cho dữ liệu cũ, không còn dùng), khoản thu liên quan.
    """
    __tablename__ = "event_detail"
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), primary_key=True)
    loai = db.Column(db.String(100), nullable=False, default="other")
    noi_dung = db.Column(db.Text, nullable=False, default="")
    dia_diem = db.Column(db.String(300), nullable=False, default="")
    anh_dai_dien = db.Column(db.String(255), nullable=False, default="")
    han_dang_ky = db.Column(db.DateTime)
    so_nguoi_toi_da = db.Column(db.Integer)
    phu_trach_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    fund_id = db.Column(db.Integer, db.ForeignKey("fund_collection.id"))
    tat_ca_thanh_vien = db.Column(db.Boolean, nullable=False, default=True)
    event = db.relationship("Event", backref=db.backref("detail", uselist=False))
    __table_args__ = (
        db.CheckConstraint("so_nguoi_toi_da IS NULL OR so_nguoi_toi_da > 0", name="ck_event_capacity"),
    )


class EventTargetBan(db.Model):
    """Ban được phép đăng ký sự kiện (khi sự kiện không mở cho tất cả)."""
    __tablename__ = "event_target_ban"
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), primary_key=True)
    ban_id = db.Column(db.Integer, db.ForeignKey("ban.id"), primary_key=True)


class EventRegistration(db.Model):
    """Đăng ký tham gia sự kiện: registered | waitlist | cancelled."""
    __tablename__ = "event_registration"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    trang_thai = db.Column(db.String(12), nullable=False, default="registered")
    dang_ky_luc = db.Column(db.DateTime, nullable=False, default=utcnow)
    huy_luc = db.Column(db.DateTime)
    user = db.relationship("User")
    event = db.relationship("Event", backref="registrations")
    __table_args__ = (
        db.UniqueConstraint("event_id", "user_id", name="uq_event_registration_member"),
        db.CheckConstraint("trang_thai IN ('registered','waitlist','cancelled')", name="ck_event_registration_status"),
        db.Index("ix_event_registration_event_status", "event_id", "trang_thai"),
    )


class Attendance(db.Model):
    """Điểm danh của một lượt đăng ký: on_time | late | excused | absent | pending."""
    __tablename__ = "attendance"
    id = db.Column(db.Integer, primary_key=True)
    registration_id = db.Column(db.Integer, db.ForeignKey("event_registration.id"), nullable=False, unique=True)
    trang_thai = db.Column(db.String(12), nullable=False, default="pending")
    checkin_luc = db.Column(db.DateTime)
    checkin_boi_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    ghi_chu = db.Column(db.Text, nullable=False, default="")
    registration = db.relationship("EventRegistration", backref=db.backref("attendance", uselist=False))
    __table_args__ = (
        db.CheckConstraint("trang_thai IN ('on_time','late','excused','absent','pending')", name="ck_attendance_status"),
    )


class QRCheckinLog(db.Model):
    """Nhật ký mỗi lần quét QR điểm danh (phục vụ đối soát)."""
    __tablename__ = "qr_checkin_log"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    checkin_luc = db.Column(db.DateTime, nullable=False, default=utcnow)


class EventFeedback(db.Model):
    """Đánh giá 1–5 sao và bình luận của thành viên sau sự kiện."""
    __tablename__ = "event_feedback"
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.Integer, db.ForeignKey("event.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    danh_gia = db.Column(db.Integer, nullable=False)
    binh_luan = db.Column(db.Text, nullable=False, default="")
    tao_luc = db.Column(db.DateTime, nullable=False, default=utcnow)
    __table_args__ = (
        db.UniqueConstraint("event_id", "user_id", name="uq_event_feedback_member"),
        db.CheckConstraint("danh_gia BETWEEN 1 AND 5", name="ck_event_feedback_rating"),
    )


class AuditLog(db.Model):
    """Nhật ký thao tác: ai làm gì, trên đối tượng nào, lúc nào (không lưu mật khẩu/nội dung nhạy
    cảm).
    """
    __tablename__ = "audit_log"
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    action = db.Column(db.String(100), nullable=False, index=True)
    object_type = db.Column(db.String(100), nullable=False)
    object_id = db.Column(db.String(100), nullable=False)
    details = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class AIProposal(db.Model):
    """An AI suggestion is inert until a board member explicitly approves it."""

    __tablename__ = "ai_proposal"
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False)
    request_text = db.Column(db.Text, nullable=False)
    payload = db.Column(db.JSON, nullable=False)
    provider = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(12), nullable=False, default="pending", index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    reviewed_at = db.Column(db.DateTime)
    result_ids = db.Column(db.JSON, nullable=False, default=list)
    __table_args__ = (
        db.CheckConstraint("kind IN ('event','task')", name="ck_ai_proposal_kind"),
        db.CheckConstraint("status IN ('pending','approved','rejected')", name="ck_ai_proposal_status"),
        db.Index("ix_ai_proposal_creator_status", "created_by_id", "status"),
    )


class EmailProposal(db.Model):
    """Email draft: chat creation never sends; a separate BDH action does."""

    __tablename__ = "email_proposal"
    id = db.Column(db.Integer, primary_key=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    recipient_email = db.Column(db.String(120), nullable=False)
    request_text = db.Column(db.Text, nullable=False)
    subject = db.Column(db.String(180), nullable=False)
    body = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(12), nullable=False, default="pending", index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    reviewed_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    reviewed_at = db.Column(db.DateTime)
    sent_at = db.Column(db.DateTime)
    __table_args__ = (
        db.CheckConstraint("status IN ('pending','sending','sent','failed','rejected')",
                           name="ck_email_proposal_status"),
        db.Index("ix_email_proposal_creator_status", "created_by_id", "status"),
    )


class EmailTemplate(db.Model):
    """Mẫu email trong kho mẫu.

    - `system_key` có giá trị: mẫu của email tự động (xác minh email, giao task…)
      và mẫu chung `general`; chỉ sửa nội dung, không xóa/đổi tên.
    - `system_key` rỗng: mẫu do BĐH tạo, AI tìm theo tên/từ khóa khi soạn nháp.
    """

    __tablename__ = "email_template"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    name_key = db.Column(db.String(100), unique=True, nullable=False)
    system_key = db.Column(db.String(40), unique=True)
    keywords = db.Column(db.String(300), nullable=False, default="")
    description = db.Column(db.String(300), nullable=False, default="")
    subject = db.Column(db.String(180), nullable=False)
    body = db.Column(db.Text, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow,
                           onupdate=utcnow)


class ProposalTemplate(db.Model):
    """Mẫu sự kiện / mẫu chia task trong kho mẫu, AI dùng khi lập đề xuất.

    `content` (JSON):
      - kind="event": {"name", "content", "type", "location", "duration_hours"}
      - kind="task":  {"tasks": [{"name", "description", "days_before"}]}
    `is_default=True`: mẫu chung, dùng khi không tìm thấy mẫu phù hợp hơn.
    """

    __tablename__ = "proposal_template"
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    name_key = db.Column(db.String(100), nullable=False)
    keywords = db.Column(db.String(300), nullable=False, default="")
    event_type = db.Column(db.String(100), nullable=False, default="")
    content = db.Column(db.JSON, nullable=False)
    is_default = db.Column(db.Boolean, nullable=False, default=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    __table_args__ = (
        db.UniqueConstraint("kind", "name_key", name="uq_proposal_template_name"),
        db.CheckConstraint("kind IN ('event','task')", name="ck_proposal_template_kind"),
    )


class AssistantPending(db.Model):
    """The incomplete natural-language proposal currently being clarified."""

    __tablename__ = "assistant_pending"
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    kind = db.Column(db.String(10), nullable=False)
    text = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow,
                           onupdate=utcnow)


class ConversationPending(db.Model):
    """A pending clarification scoped to one conversation tab."""

    __tablename__ = "conversation_pending"
    conversation_id = db.Column(db.Integer, db.ForeignKey("chat_conversation.id"), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    kind = db.Column(db.String(10), nullable=False)
    text = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow,
                           onupdate=utcnow)


class AIReportSnapshot(db.Model):
    """Immutable reviewed numbers and source rows for export/email."""

    __tablename__ = "ai_report_snapshot"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    period = db.Column(db.String(10), nullable=False)
    payload = db.Column(db.JSON, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class MemberPlanningProfile(db.Model):
    """Member-provided task preferences; absent rows use conservative defaults."""

    __tablename__ = "member_planning_profile"
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    skills = db.Column(db.JSON, nullable=False, default=list)
    max_active_tasks = db.Column(db.Integer, nullable=False, default=3)
    unavailable_until = db.Column(db.Date)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow,
                           onupdate=utcnow)
    __table_args__ = (
        db.CheckConstraint("max_active_tasks BETWEEN 1 AND 10",
                           name="ck_member_planning_capacity"),
    )


class AIUsageLog(db.Model):
    """Successful model calls, without prompts, replies, secrets or price guesses."""

    __tablename__ = "ai_usage_log"
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    provider = db.Column(db.String(20), nullable=False)
    model = db.Column(db.String(100), nullable=False)
    purpose = db.Column(db.String(20), nullable=False)
    input_tokens = db.Column(db.Integer, nullable=False, default=0)
    output_tokens = db.Column(db.Integer, nullable=False, default=0)
    total_tokens = db.Column(db.Integer, nullable=False, default=0)
    elapsed_ms = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
