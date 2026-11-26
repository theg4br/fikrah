"""Add a default-false favorite flag to saved notes."""

import sqlalchemy as sa
from alembic import op

revision = "20261126_02"
down_revision = "20261126_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notes", sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("notes", "is_favorite")
