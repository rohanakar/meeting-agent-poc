"""drop identity: every bot is signed in

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("meetings", "identity")


def downgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column("identity", sa.String(length=16), server_default="signed_in", nullable=False),
    )
