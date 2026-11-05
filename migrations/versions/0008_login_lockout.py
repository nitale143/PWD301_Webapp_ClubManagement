"""Tạm khóa đăng nhập lưu trong database.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-06

Thêm `user.dang_nhap_sai` (số lần sai liên tiếp) và `user.khoa_dang_nhap_den` (khóa tới lúc nào).
Trước đây bộ đếm nằm trong RAM của từng worker nên mất khi restart và mỗi worker đếm riêng.
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user') as batch:
        batch.add_column(sa.Column('dang_nhap_sai', sa.Integer(), nullable=False, server_default='0'))
        batch.add_column(sa.Column('khoa_dang_nhap_den', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('user') as batch:
        batch.drop_column('khoa_dang_nhap_den')
        batch.drop_column('dang_nhap_sai')
