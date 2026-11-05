"""Một nguồn duy nhất cho tình trạng hoạt động (50% task + 50% điểm danh).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-05

- `member_activity`: tạo lại theo (thành viên, kỳ), bỏ cột `ban_id`, `phan_tram`; thêm
  `tong_su_kien`, `su_kien_co_mat`, `diem`. Dữ liệu cũ chỉ là kết quả tính toán nên không chuyển;
  chạy `flask --app run.py recompute-activity` (hoặc `init-db`) sau khi nâng cấp để tính lại.
- Xóa cột `user.hoat_dong_status` (không có code nào cập nhật, luôn là giá trị mặc định).
- Xóa bảng `activity_point` (điểm cộng/trừ không còn dùng để xếp loại) và `club_setting`
  (chỉ chứa bảng điểm/ngưỡng của hệ thống điểm cũ; ngưỡng mới nằm trong app/config.py).

Hạ cấp (downgrade) tạo lại cấu trúc cũ nhưng KHÔNG khôi phục dữ liệu đã xóa.
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table('member_activity')
    op.create_table(
        'member_activity',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('period_id', sa.Integer(), sa.ForeignKey('fund_period.id'), nullable=False),
        sa.Column('tong_task', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('task_hoan_thanh', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tong_su_kien', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('su_kien_co_mat', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('diem', sa.Float(), nullable=True),
        sa.Column('trang_thai', sa.String(20), nullable=False, server_default='chua_xep_loai'),
        sa.Column('cap_nhat_luc', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', 'period_id', name='uq_activity_user_period'),
    )
    with op.batch_alter_table('activity_point') as batch:
        batch.drop_index('ix_activity_point_member_time')
    op.drop_table('activity_point')
    op.drop_table('club_setting')
    with op.batch_alter_table('user') as batch:
        batch.drop_column('hoat_dong_status')


def downgrade():
    with op.batch_alter_table('user') as batch:
        batch.add_column(sa.Column('hoat_dong_status', sa.String(20), nullable=True))
    op.create_table(
        'club_setting',
        sa.Column('key', sa.String(100), primary_key=True),
        sa.Column('value', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_table(
        'activity_point',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('event_id', sa.Integer(), sa.ForeignKey('event.id'), nullable=True),
        sa.Column('so_diem', sa.Integer(), nullable=False),
        sa.Column('loai', sa.String(12), nullable=False),
        sa.Column('ly_do', sa.Text(), nullable=False),
        sa.Column('tao_boi_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('tao_luc', sa.DateTime(), nullable=False),
        sa.CheckConstraint('length(trim(ly_do)) > 0', name='ck_activity_point_reason'),
    )
    with op.batch_alter_table('activity_point') as batch:
        batch.create_index('ix_activity_point_member_time', ['user_id', 'tao_luc'], unique=False)
    op.drop_table('member_activity')
    op.create_table(
        'member_activity',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('ban_id', sa.Integer(), sa.ForeignKey('ban.id'), nullable=False),
        sa.Column('period_id', sa.Integer(), sa.ForeignKey('fund_period.id'), nullable=False),
        sa.Column('tong_task', sa.Integer(), nullable=True),
        sa.Column('task_hoan_thanh', sa.Integer(), nullable=True),
        sa.Column('phan_tram', sa.Float(), nullable=True),
        sa.Column('trang_thai', sa.String(20), nullable=True),
        sa.Column('cap_nhat_luc', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('user_id', 'ban_id', 'period_id', name='uq_activity_user_ban_period'),
    )
