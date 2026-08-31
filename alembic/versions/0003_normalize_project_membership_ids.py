"""normalize legacy project membership ids

Revision ID: 0003_membership_uuid
Revises: 0002_project_access
Create Date: 2026-08-23
"""
from __future__ import annotations

from uuid import UUID, uuid4

import sqlalchemy as sa

from alembic import op

revision = "0003_membership_uuid"
down_revision = "0002_project_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    membership_ids = bind.execute(sa.text("SELECT id FROM project_memberships")).scalars()
    for membership_id in membership_ids:
        try:
            UUID(membership_id)
        except (TypeError, ValueError, AttributeError):
            bind.execute(
                sa.text("UPDATE project_memberships SET id = :new_id WHERE id = :old_id"),
                {"new_id": str(uuid4()), "old_id": membership_id},
            )


def downgrade() -> None:
    # UUID normalization is data repair and must not be reversed.
    pass
