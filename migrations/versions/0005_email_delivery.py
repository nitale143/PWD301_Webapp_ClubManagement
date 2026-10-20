"""Durable email delivery tracking and retry metadata.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "email_delivery",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column("recipient_user_id", sa.Integer(), nullable=True),
        sa.Column("recipient_email", sa.String(length=254), nullable=False),
        sa.Column("sender_email", sa.String(length=254), nullable=False),
        sa.Column("last_sender_email", sa.String(length=254), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("retry_allowed", sa.Boolean(), nullable=False),
        sa.Column("sensitive_content", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("last_failure_phase", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("body_expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("dev_saved_at", sa.DateTime(), nullable=True),
        sa.Column("last_attempt_by_id", sa.Integer(), nullable=True),
        sa.Column("unknown_confirmed_by_id", sa.Integer(), nullable=True),
        sa.Column("unknown_confirmed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["last_attempt_by_id"], ["user.id"]),
        sa.ForeignKeyConstraint(["unknown_confirmed_by_id"], ["user.id"]),
        sa.UniqueConstraint("idempotency_key", name="uq_email_delivery_idempotency_key"),
        sa.UniqueConstraint("message_id", name="uq_email_delivery_message_id"),
        sa.CheckConstraint(
            "status IN ('pending','sending','sent','failed','unknown','skipped','dev-saved')",
            name="ck_email_delivery_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_email_delivery_attempt_count"),
    )
    op.create_index("ix_email_delivery_status", "email_delivery", ["status"])
    op.create_index("ix_email_delivery_created_at", "email_delivery", ["created_at"])
    op.create_index("ix_email_delivery_status_created", "email_delivery", ["status", "created_at"])


def downgrade():
    op.drop_index("ix_email_delivery_status_created", table_name="email_delivery")
    op.drop_index("ix_email_delivery_created_at", table_name="email_delivery")
    op.drop_index("ix_email_delivery_status", table_name="email_delivery")
    op.drop_table("email_delivery")
