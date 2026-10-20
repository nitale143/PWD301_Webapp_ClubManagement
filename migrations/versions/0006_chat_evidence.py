"""Nguồn kiểm chứng của câu trả lời AI, chỉ chủ cuộc trò chuyện được xem."""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("chat_evidence",
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("chat_message.id"), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("role_scope", sa.String(20), nullable=False),
        sa.Column("permissions", sa.Integer(), nullable=False),
        sa.Column("intent", sa.String(80), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("query_labels", sa.JSON(), nullable=False),
        sa.Column("answer_sha256", sa.String(64), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("query_version", sa.String(24), nullable=False))
    op.create_index("ix_chat_evidence_user_id", "chat_evidence", ["user_id"])


def downgrade():
    op.drop_index("ix_chat_evidence_user_id", table_name="chat_evidence")
    op.drop_table("chat_evidence")
