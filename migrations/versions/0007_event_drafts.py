"""Bản nháp sự kiện thay cho đề xuất AI.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-05

- Tạo `event_draft`: bản nháp sự kiện + task do trợ lý lập, lưu cả khi còn thiếu thông tin.
- Xóa `ai_proposal` (đề xuất phải đủ thông tin mới lập được), `assistant_pending` và
  `conversation_pending` (câu đề xuất đang hỏi dở). Đề xuất chưa duyệt không được chuyển sang:
  đó chỉ là bản xem trước, chưa ghi dữ liệu thật; người dùng mô tả lại để AI lập bản nháp mới.
  Đề xuất đã duyệt vẫn còn dấu vết ở sự kiện/task đã tạo và trong audit_log.

Hạ cấp tạo lại ba bảng cũ (rỗng) và xóa `event_draft`.
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'event_draft',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=True),
        sa.Column('event_id', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('request_text', sa.Text(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('reviewed_by_id', sa.Integer(), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('result_event_id', sa.Integer(), nullable=True),
        sa.CheckConstraint("status IN ('draft','approved','discarded')", name='ck_event_draft_status'),
        sa.ForeignKeyConstraint(['created_by_id'], ['user.id']),
        sa.ForeignKeyConstraint(['conversation_id'], ['chat_conversation.id']),
        sa.ForeignKeyConstraint(['event_id'], ['event.id']),
        sa.ForeignKeyConstraint(['reviewed_by_id'], ['user.id']),
        sa.ForeignKeyConstraint(['result_event_id'], ['event.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('event_draft') as batch:
        batch.create_index('ix_event_draft_conversation_id', ['conversation_id'])
        batch.create_index('ix_event_draft_status_updated', ['status', 'updated_at'])
    op.drop_table('conversation_pending')
    op.drop_table('assistant_pending')
    with op.batch_alter_table('ai_proposal') as batch:
        batch.drop_index('ix_ai_proposal_status')
        batch.drop_index('ix_ai_proposal_creator_status')
    op.drop_table('ai_proposal')


def downgrade():
    op.create_table(
        'ai_proposal',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('request_text', sa.Text(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('provider', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('reviewed_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('result_ids', sa.JSON(), nullable=False),
        sa.CheckConstraint("kind IN ('event','task')", name='ck_ai_proposal_kind'),
        sa.CheckConstraint("status IN ('pending','approved','rejected')", name='ck_ai_proposal_status'),
        sa.ForeignKeyConstraint(['created_by_id'], ['user.id']),
        sa.ForeignKeyConstraint(['reviewed_by_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('ai_proposal') as batch:
        batch.create_index('ix_ai_proposal_creator_status', ['created_by_id', 'status'])
        batch.create_index('ix_ai_proposal_status', ['status'])
    op.create_table(
        'assistant_pending',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('user_id'),
    )
    op.create_table(
        'conversation_pending',
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['chat_conversation.id']),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('conversation_id'),
    )
    with op.batch_alter_table('event_draft') as batch:
        batch.drop_index('ix_event_draft_status_updated')
        batch.drop_index('ix_event_draft_conversation_id')
    op.drop_table('event_draft')
