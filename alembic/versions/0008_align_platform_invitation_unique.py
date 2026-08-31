"""Align the platform invitation token uniqueness with SQLAlchemy metadata.

Revision ID: 0008_platform_invite_unique
Revises: 0007_platform_customer_split
"""

from __future__ import annotations

from sqlalchemy import inspect

from alembic import op

revision = "0008_platform_invite_unique"
down_revision = "0007_platform_customer_split"
branch_labels = None
depends_on = None


def _has_unique_constraint(name: str) -> bool:
    inspector = inspect(op.get_bind())
    return any(
        constraint.get("name") == name
        for constraint in inspector.get_unique_constraints("platform_invitations")
    )


def upgrade() -> None:
    # 0007 created both a unique constraint and a unique index for token_hash.
    # mapped_column(unique=True, index=True) is represented by the unique index,
    # so the additional PostgreSQL constraint is redundant.
    name = "platform_invitations_token_hash_key"
    if _has_unique_constraint(name):
        op.drop_constraint(name, "platform_invitations", type_="unique")


def downgrade() -> None:
    name = "platform_invitations_token_hash_key"
    if not _has_unique_constraint(name):
        op.create_unique_constraint(name, "platform_invitations", ["token_hash"])
