"""grant company administrators read access to project execution details

Revision ID: 0009_company_admin_read
Revises: 0008_platform_invite_unique
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0009_company_admin_read"
down_revision = "0008_platform_invite_unique"
branch_labels = None
depends_on = None

READ_PERMISSIONS = (
    "run.view",
    "approval.view",
    "import.view",
    "acceptance.view",
    "artifact.view",
)


def upgrade() -> None:
    bind = op.get_bind()
    for permission in READ_PERMISSIONS:
        bind.execute(
            sa.text(
                "INSERT INTO role_permissions (role_code, permission_code) "
                "VALUES ('company_admin', :permission) ON CONFLICT DO NOTHING"
            ),
            {"permission": permission},
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE role_code = 'company_admin' "
            "AND permission_code IN :permissions"
        ).bindparams(sa.bindparam("permissions", expanding=True)),
        {"permissions": READ_PERMISSIONS},
    )
