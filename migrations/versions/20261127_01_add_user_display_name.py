"""Add account display names without inventing names for existing users."""

import sqlalchemy as sa
from alembic import op

revision = "20261127_01"
down_revision = "20261126_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("name", sa.String(120), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "name")
