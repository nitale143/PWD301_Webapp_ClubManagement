"""Trạng thái sự kiện: chuyển tay + hệ thống tự rà soát theo giờ.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-05

- Chuẩn hóa giá trị cũ không còn dùng: 'completed' → 'da_ket_thuc'; 'open', 'draft', rỗng hoặc
  giá trị lạ → 'sap_dien_ra' (lần rà soát đầu tiên sẽ chuyển tiếp theo giờ).
- `event.trang_thai` NOT NULL + CheckConstraint 4 giá trị.
- Thêm `event.trang_thai_doi_tay_luc`: lần gần nhất BĐH đổi trạng thái bằng tay.

Sau khi nâng cấp, chạy `flask --app run.py sync-event-status` (hoặc `init-db`) để các sự kiện
đã qua giờ được chuyển ngay, rồi `recompute-activity`.
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

STATUSES = ("sap_dien_ra", "dang_dien_ra", "da_ket_thuc", "cancelled")


def upgrade():
    op.execute("UPDATE event SET trang_thai = 'da_ket_thuc' WHERE trang_thai = 'completed'")
    op.execute("UPDATE event SET trang_thai = 'sap_dien_ra' WHERE trang_thai IS NULL OR trang_thai NOT IN "
               "('sap_dien_ra', 'dang_dien_ra', 'da_ket_thuc', 'cancelled')")
    with op.batch_alter_table('event') as batch:
        batch.add_column(sa.Column('trang_thai_doi_tay_luc', sa.DateTime(), nullable=True))
        batch.alter_column('trang_thai', existing_type=sa.String(20), nullable=False)
        batch.create_check_constraint(
            'ck_event_status',
            "trang_thai IN ('sap_dien_ra','dang_dien_ra','da_ket_thuc','cancelled')")


def downgrade():
    with op.batch_alter_table('event') as batch:
        batch.drop_constraint('ck_event_status', type_='check')
        batch.alter_column('trang_thai', existing_type=sa.String(20), nullable=True)
        batch.drop_column('trang_thai_doi_tay_luc')
