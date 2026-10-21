"""attendees and speaker emails

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column(
            "attendees",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )
    op.add_column(
        "transcript_segments", sa.Column("speaker_email", sa.String(length=320), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("transcript_segments", "speaker_email")
    op.drop_column("meetings", "attendees")
