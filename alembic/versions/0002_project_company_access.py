"""company identity and project-level access

Revision ID: 0002_project_access
Revises: 0001_multitenant
Create Date: 2026-08-23
"""
from __future__ import annotations

from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "0002_project_access"
down_revision = "0001_multitenant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    duplicate_users = bind.execute(sa.text(
        "SELECT user_id FROM tenant_memberships WHERE status = 'active' "
        "GROUP BY user_id HAVING COUNT(*) > 1"
    )).fetchall()
    if duplicate_users:
        ids = ", ".join(row[0] for row in duplicate_users)
        raise RuntimeError(f"一个账号存在多个有效公司关系，请先处理后再迁移: {ids}")

    bind.execute(sa.text(
        "UPDATE tenant_memberships SET role = 'tenant_member' "
        "WHERE role NOT IN ('tenant_admin')"
    ))
    bind.execute(sa.text(
        "UPDATE user_invitations SET role = 'tenant_member' "
        "WHERE role NOT IN ('tenant_admin') AND status = 'pending'"
    ))

    if "project_memberships" not in tables:
        op.create_table(
            "project_memberships",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("implementation_projects.id"), nullable=False),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("customer_tenants.id"), nullable=False),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("project_role", sa.String(40), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="active"),
            sa.Column("granted_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("project_id", "user_id", name="uq_project_membership_user"),
        )
        op.create_index("ix_project_memberships_project_id", "project_memberships", ["project_id"])
        op.create_index("ix_project_memberships_tenant_id", "project_memberships", ["tenant_id"])
        op.create_index("ix_project_memberships_user_id", "project_memberships", ["user_id"])
        op.create_index("ix_project_memberships_status", "project_memberships", ["status"])

    if "project_company_collaborations" not in tables:
        op.create_table(
            "project_company_collaborations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("implementation_projects.id"), nullable=False),
            sa.Column("owner_tenant_id", sa.String(36), sa.ForeignKey("customer_tenants.id"), nullable=False),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("customer_tenants.id"), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("invited_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("accepted_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("project_id", "tenant_id", name="uq_project_collaboration_tenant"),
        )
        op.create_index("ix_project_collaborations_project_id", "project_company_collaborations", ["project_id"])
        op.create_index("ix_project_collaborations_tenant_id", "project_company_collaborations", ["tenant_id"])
        op.create_index("ix_project_collaborations_status", "project_company_collaborations", ["status"])

    # Least-privilege backfill: only creators/owners keep project access.
    legacy_projects = bind.execute(sa.text(
        "SELECT p.id AS project_id, p.tenant_id, COALESCE(p.owner_user_id, p.created_by) AS user_id "
        "FROM implementation_projects p "
        "WHERE COALESCE(p.owner_user_id, p.created_by) IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM project_memberships pm WHERE pm.project_id = p.id "
        "AND pm.user_id = COALESCE(p.owner_user_id, p.created_by))"
    )).mappings()
    insert_membership = sa.text(
        "INSERT INTO project_memberships "
        "(id, project_id, tenant_id, user_id, project_role, status, granted_by, created_at, updated_at) "
        "VALUES (:id, :project_id, :tenant_id, :user_id, 'project_manager', 'active', :user_id, "
        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    for project in legacy_projects:
        bind.execute(insert_membership, {"id": str(uuid4()), **project})

    if bind.dialect.name == "postgresql":
        # One active company per account. Disabled historical rows remain for audit.
        membership_indexes = {
            index["name"] for index in sa.inspect(bind).get_indexes("tenant_memberships")
        }
        if "uq_active_company_per_user" not in membership_indexes:
            op.create_index(
                "uq_active_company_per_user", "tenant_memberships", ["user_id"], unique=True,
                postgresql_where=sa.text("status = 'active'"),
            )
        # RLS is an additional production guard. The application account must not have BYPASSRLS.
        op.execute("ALTER TABLE implementation_projects ENABLE ROW LEVEL SECURITY")
        op.execute("DROP POLICY IF EXISTS project_tenant_policy ON implementation_projects")
        op.execute("""
            CREATE POLICY project_tenant_policy ON implementation_projects
            USING (
              current_setting('app.is_platform_admin', true) = 'true'
              OR (
                (
                  tenant_id = current_setting('app.current_tenant_id', true)
                  OR EXISTS (
                    SELECT 1 FROM project_company_collaborations pc
                    WHERE pc.project_id = implementation_projects.id
                      AND pc.tenant_id = current_setting('app.current_tenant_id', true)
                      AND pc.status = 'active'
                  )
                )
                AND (
                  current_setting('app.current_company_role', true) = 'tenant_admin'
                  OR EXISTS (
                    SELECT 1 FROM project_memberships pm
                    WHERE pm.project_id = implementation_projects.id
                      AND pm.user_id = current_setting('app.current_user_id', true)
                      AND pm.status = 'active'
                  )
                )
              )
            )
        """)
        direct_children = {
            "project_documents": "project_id",
            "agent_runs": "project_id",
            "import_jobs": "project_id",
        }
        for table, column in direct_children.items():
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"DROP POLICY IF EXISTS {table}_project_policy ON {table}")
            op.execute(f"""
                CREATE POLICY {table}_project_policy ON {table}
                USING (EXISTS (
                  SELECT 1 FROM implementation_projects p
                  WHERE p.id = {table}.{column}
                ))
            """)
        for table in ("agent_steps", "approval_requests"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"DROP POLICY IF EXISTS {table}_run_policy ON {table}")
            op.execute(f"""
                CREATE POLICY {table}_run_policy ON {table}
                USING (EXISTS (
                  SELECT 1 FROM agent_runs r
                  WHERE r.id = {table}.run_id
                ))
            """)


def downgrade() -> None:
    raise RuntimeError("Project access migration is intentionally irreversible")
