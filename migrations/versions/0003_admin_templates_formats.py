"""Tài khoản quản trị hệ thống, kho mẫu (email / sự kiện / task), chuẩn hóa SĐT và Facebook.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-20

- Thêm role ADMIN (Quản trị hệ thống, rank -1) vào bảng `role`.
- `email_template`: thêm system_key (mẫu email tự động), keywords, description;
  created_by_id cho phép rỗng (mẫu hệ thống không có người tạo).
- Tạo bảng `proposal_template` (mẫu sự kiện / mẫu chia task cho AI).
- Dữ liệu cũ: SĐT dạng 0xxxxxxxxx → +84xxxxxxxxx; link Facebook dạng
  http(s)://(www.|m.)facebook.com/<id>[/] → https://facebook.com/<id>.
  Giá trị không chuyển được thì giữ nguyên.

Mẫu mặc định (email hệ thống, mẫu chung) được nạp bằng `flask init-db`
(hoặc `flask seed-templates`), không nạp trong migration.
"""
import re

from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

# Bit quyền – khớp app.models.Permission tại thời điểm viết migration
APPROVE_MEMBERS, MANAGE_MEMBERS, MANAGE_EVENTS, MANAGE_FUNDS = 1, 2, 4, 8
ASSIGN_ROLES, BOARD, MANAGE_TEMPLATES = 16, 64, 128
ADMIN_PERMISSIONS = (APPROVE_MEMBERS | MANAGE_MEMBERS | MANAGE_EVENTS | MANAGE_FUNDS
                     | ASSIGN_ROLES | BOARD | MANAGE_TEMPLATES)

_FACEBOOK = re.compile(r"^https?://(?:www\.|m\.)?facebook\.com/([A-Za-z0-9.]{1,50})/?$")


def _phone(value):
    digits = re.sub(r"[\s.\-()]", "", value or "")
    if digits.startswith("+84"):
        digits = digits[3:]
    elif digits.startswith("84") and len(digits) == 11:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 10:
        digits = digits[1:]
    return "+84" + digits if re.fullmatch(r"[1-9]\d{8}", digits) else None


def upgrade():
    bind = op.get_bind()
    role = sa.table("role", sa.column("name"), sa.column("label"), sa.column("rank"),
                    sa.column("default"), sa.column("permissions"))
    if not bind.execute(sa.text("SELECT 1 FROM role WHERE name = 'ADMIN'")).first():
        op.bulk_insert(role, [{"name": "ADMIN", "label": "Quản trị hệ thống", "rank": -1,
                               "default": False, "permissions": ADMIN_PERMISSIONS}])

    with op.batch_alter_table("email_template") as batch:
        batch.add_column(sa.Column("system_key", sa.String(40), nullable=True))
        batch.add_column(sa.Column("keywords", sa.String(300), nullable=False, server_default=""))
        batch.add_column(sa.Column("description", sa.String(300), nullable=False, server_default=""))
        batch.alter_column("created_by_id", existing_type=sa.Integer(), nullable=True)
        batch.create_unique_constraint("uq_email_template_system_key", ["system_key"])

    op.create_table(
        "proposal_template",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_key", sa.String(100), nullable=False),
        sa.Column("keywords", sa.String(300), nullable=False, server_default=""),
        sa.Column("event_type", sa.String(100), nullable=False, server_default=""),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("kind", "name_key", name="uq_proposal_template_name"),
        sa.CheckConstraint("kind IN ('event','task')", name="ck_proposal_template_kind"),
    )

    # Chuẩn hóa dữ liệu liên hệ cũ theo định dạng mới
    rows = bind.execute(sa.text('SELECT id, sdt, facebook_link FROM "user"')).fetchall()
    for user_id, phone, facebook in rows:
        changes = {}
        new_phone = _phone(phone)
        if new_phone and new_phone != phone:
            changes["sdt"] = new_phone
        match = _FACEBOOK.match((facebook or "").strip())
        if match and facebook != "https://facebook.com/" + match.group(1):
            changes["facebook_link"] = "https://facebook.com/" + match.group(1)
        if changes:
            assignments = ", ".join(f"{column} = :{column}" for column in changes)
            bind.execute(sa.text(f'UPDATE "user" SET {assignments} WHERE id = :id'),
                         {**changes, "id": user_id})


def downgrade():
    op.drop_table("proposal_template")
    op.execute("DELETE FROM email_template WHERE system_key IS NOT NULL")
    with op.batch_alter_table("email_template") as batch:
        batch.drop_constraint("uq_email_template_system_key", type_="unique")
        batch.drop_column("description")
        batch.drop_column("keywords")
        batch.drop_column("system_key")
    op.execute("DELETE FROM role WHERE name = 'ADMIN'")
