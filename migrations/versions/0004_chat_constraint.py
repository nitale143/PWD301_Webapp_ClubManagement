"""Name the legacy chat-conversation uniqueness constraint.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-20

Some deployed SQLite databases created this table with an unnamed
UNIQUE(user_id, legacy_marker), while newer schemas already use the named
constraint. Rebuild only when needed so both layouts converge without changing
rows, columns, indexes, or foreign-key definitions.
"""
from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


_CONSTRAINT_NAME = "uq_chat_conversation_legacy"
_COLUMNS = ("user_id", "legacy_marker")


def _is_target_constraint(constraint):
    return (isinstance(constraint, sa.UniqueConstraint)
            and tuple(column.name for column in constraint.columns) == _COLUMNS)


def upgrade():
    bind = op.get_bind()
    metadata = sa.MetaData()
    table = sa.Table("chat_conversation", metadata, autoload_with=bind)
    matching = [constraint for constraint in table.constraints if _is_target_constraint(constraint)]

    # A correctly named constraint is already the target schema. Re-running this
    # migration's upgrade function is safe and does not rebuild an up-to-date table.
    if len(matching) == 1 and matching[0].name == _CONSTRAINT_NAME:
        return

    for constraint in matching:
        table.constraints.remove(constraint)
    table.append_constraint(sa.UniqueConstraint(*_COLUMNS, name=_CONSTRAINT_NAME))

    # Supplying the reflected table after replacing only the target UNIQUE lets
    # Alembic retain the deployed columns, indexes, checks, and outgoing FKs.
    with op.batch_alter_table("chat_conversation", copy_from=table, recreate="always"):
        pass


def downgrade():
    """Restore an unnamed constraint while retaining the uniqueness guarantee."""
    bind = op.get_bind()
    metadata = sa.MetaData()
    table = sa.Table("chat_conversation", metadata, autoload_with=bind)
    matching = [constraint for constraint in table.constraints if _is_target_constraint(constraint)]
    if len(matching) == 1 and matching[0].name is None:
        return

    for constraint in matching:
        table.constraints.remove(constraint)
    table.append_constraint(sa.UniqueConstraint(*_COLUMNS))
    with op.batch_alter_table("chat_conversation", copy_from=table, recreate="always"):
        pass
