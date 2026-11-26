"""Add keyed OTP digests and durable failed-attempt counters to email actions."""

import sqlalchemy as sa
from alembic import op

revision = "20261126_01"
down_revision = "20261121_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_action_tokens", sa.Column("otp_digest", sa.String(64), nullable=True))
    op.add_column(
        "email_action_tokens",
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("email_action_tokens", "failed_attempts")
    op.drop_column("email_action_tokens", "otp_digest")
