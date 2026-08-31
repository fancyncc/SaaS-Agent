"""split platform administration from customer workspaces

Revision ID: 0007_platform_customer_split
Revises: 0006_state_machine
"""

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "0007_platform_customer_split"
down_revision = "0006_state_machine"
branch_labels = None
depends_on = None

PLATFORM_ROLES = {
    "platform_super_admin": "平台超级管理员",
    "platform_operator": "平台运营人员",
    "platform_support": "平台支持人员",
    "platform_auditor": "平台审计人员",
}

ROLE_PERMISSIONS = {
    "platform_super_admin": {
        "company.create", "company.view", "company.update", "audit.view", "audit.export",
        "system.view", "support.request", "support.revoke", "platform.staff.manage",
        "user.view", "user.update", "user.transfer", "session.revoke", "customer.inspect",
        "evaluation.run",
    },
    "platform_operator": {
        "company.create", "company.view", "company.update", "audit.view",
        "user.view", "user.update", "user.transfer", "session.revoke",
    },
    "platform_support": {"company.view", "user.view", "system.view", "support.request", "support.revoke"},
    "platform_auditor": {"company.view", "user.view", "audit.view", "audit.export", "system.view"},
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {item["name"] for item in inspector.get_columns("users")}
    auth_columns = {item["name"] for item in inspector.get_columns("auth_sessions")}
    tables = set(inspector.get_table_names())
    if "account_type" not in user_columns:
        op.add_column("users", sa.Column("account_type", sa.String(20), nullable=False, server_default="customer"))
        op.create_index("ix_users_account_type", "users", ["account_type"])

    for code, name in PLATFORM_ROLES.items():
        bind.execute(sa.text(
            "INSERT INTO role_definitions (id,code,scope,name,description,built_in,status,created_at) "
            "VALUES (:id,:code,'platform',:name,:description,true,'active',:created_at) "
            "ON CONFLICT (code) DO NOTHING"
        ), {"id": str(uuid4()), "code": code, "name": name, "description": name,
            "created_at": datetime.now(UTC)})

    permissions = sorted(set().union(*ROLE_PERMISSIONS.values()))
    for code in permissions:
        resource, action = code.split(".", 1)
        bind.execute(sa.text(
            "INSERT INTO permission_definitions (id,code,resource,action,description,risk_level) "
            "VALUES (:id,:code,:resource,:action,:description,'medium') ON CONFLICT (code) DO NOTHING"
        ), {"id": str(uuid4()), "code": code, "resource": resource, "action": action,
            "description": code})
    for role, permission_codes in ROLE_PERMISSIONS.items():
        for permission in permission_codes:
            bind.execute(sa.text(
                "INSERT INTO role_permissions (role_code,permission_code) VALUES (:role,:permission) "
                "ON CONFLICT DO NOTHING"
            ), {"role": role, "permission": permission})

    bind.execute(sa.text(
        "UPDATE platform_role_bindings SET role_code='platform_super_admin' "
        "WHERE role_code='platform_admin'"
    ))
    bind.execute(sa.text(
        "UPDATE users SET account_type='platform' WHERE is_platform_admin=true OR EXISTS ("
        "SELECT 1 FROM platform_role_bindings b WHERE b.user_id=users.id AND b.status='active')"
    ))
    bind.execute(sa.text(
        "UPDATE tenant_memberships SET status='disabled' WHERE status='active' AND user_id IN ("
        "SELECT id FROM users WHERE account_type='platform')"
    ))
    bind.execute(sa.text("UPDATE auth_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE revoked_at IS NULL"))

    if "context_type" not in auth_columns:
        op.add_column("auth_sessions", sa.Column("context_type", sa.String(20), nullable=False, server_default="customer"))
        op.create_index("ix_auth_sessions_context_type", "auth_sessions", ["context_type"])
    if not next(item for item in inspector.get_columns("auth_sessions") if item["name"] == "tenant_id")["nullable"]:
        op.alter_column("auth_sessions", "tenant_id", existing_type=sa.String(36), nullable=True)
    if not next(item for item in inspector.get_columns("audit_events") if item["name"] == "tenant_id")["nullable"]:
        op.alter_column("audit_events", "tenant_id", existing_type=sa.String(36), nullable=True)
    if not next(item for item in inspector.get_columns("evaluation_runs") if item["name"] == "tenant_id")["nullable"]:
        op.alter_column("evaluation_runs", "tenant_id", existing_type=sa.String(36), nullable=True)

    if "platform_invitations" not in tables:
        op.create_table(
            "platform_invitations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("email", sa.String(160), nullable=False),
            sa.Column("display_name", sa.String(100), nullable=False, server_default=""),
            sa.Column("role_code", sa.String(80), sa.ForeignKey("role_definitions.code"), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("invited_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        for name, columns in {
            "ix_platform_invitations_email": ["email"],
            "ix_platform_invitations_role_code": ["role_code"],
            "ix_platform_invitations_status": ["status"],
            "ix_platform_invitations_token_hash": ["token_hash"],
        }.items():
            op.create_index(name, "platform_invitations", columns, unique=name.endswith("token_hash"))

    user_checks = {item["name"] for item in inspector.get_check_constraints("users")}
    auth_checks = {item["name"] for item in inspector.get_check_constraints("auth_sessions")}
    if "ck_user_account_type" not in user_checks:
        op.create_check_constraint("ck_user_account_type", "users", "account_type IN ('platform','customer')")
    if "ck_auth_session_context" not in auth_checks:
        op.create_check_constraint("ck_auth_session_context", "auth_sessions", "context_type IN ('platform','customer')")
    if "ck_auth_session_tenant_context" not in auth_checks:
        op.create_check_constraint(
            "ck_auth_session_tenant_context", "auth_sessions",
            "(context_type='platform' AND tenant_id IS NULL) OR (context_type='customer' AND tenant_id IS NOT NULL)",
        )
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS project_scope_policy ON implementation_projects")
        op.execute("""
            CREATE POLICY project_scope_policy ON implementation_projects FOR SELECT USING (
              position('platform_super_admin' in current_setting('app.platform_roles', true)) > 0
              OR (
                (tenant_id = current_setting('app.current_tenant_id', true)
                 OR EXISTS (SELECT 1 FROM project_company_collaborations pc
                   WHERE pc.project_id=implementation_projects.id
                     AND pc.tenant_id=current_setting('app.current_tenant_id', true)
                     AND pc.status='active'))
                AND (current_setting('app.current_company_role', true)='company_admin'
                 OR EXISTS (SELECT 1 FROM project_memberships pm
                   WHERE pm.project_id=implementation_projects.id
                     AND pm.user_id=current_setting('app.current_user_id', true)
                     AND pm.status='active'))
              ) OR EXISTS (
                SELECT 1 FROM support_access_grants sag
                JOIN support_access_grant_permissions sagp ON sagp.grant_id=sag.id
                WHERE sag.requester_user_id=current_setting('app.current_user_id', true)
                  AND sag.target_project_id=implementation_projects.id
                  AND sag.status='approved' AND sag.revoked_at IS NULL
                  AND sag.expires_at > CURRENT_TIMESTAMP AND sagp.permission_code='project.view'
              )
            )
        """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS project_scope_policy ON implementation_projects")
        op.execute("""
            CREATE POLICY project_scope_policy ON implementation_projects FOR SELECT USING (
              ((tenant_id = current_setting('app.current_tenant_id', true)
                OR EXISTS (SELECT 1 FROM project_company_collaborations pc
                  WHERE pc.project_id=implementation_projects.id
                    AND pc.tenant_id=current_setting('app.current_tenant_id', true)
                    AND pc.status='active'))
               AND (current_setting('app.current_company_role', true)='company_admin'
                OR EXISTS (SELECT 1 FROM project_memberships pm
                  WHERE pm.project_id=implementation_projects.id
                    AND pm.user_id=current_setting('app.current_user_id', true)
                    AND pm.status='active')))
              OR EXISTS (SELECT 1 FROM support_access_grants sag
                JOIN support_access_grant_permissions sagp ON sagp.grant_id=sag.id
                WHERE sag.requester_user_id=current_setting('app.current_user_id', true)
                  AND sag.target_project_id=implementation_projects.id
                  AND sag.status='approved' AND sag.revoked_at IS NULL
                  AND sag.expires_at>CURRENT_TIMESTAMP AND sagp.permission_code='project.view')
            )
        """)
    op.drop_constraint("ck_auth_session_tenant_context", "auth_sessions", type_="check")
    op.drop_constraint("ck_auth_session_context", "auth_sessions", type_="check")
    op.drop_constraint("ck_user_account_type", "users", type_="check")
    for name in ["ix_platform_invitations_token_hash", "ix_platform_invitations_status",
                 "ix_platform_invitations_role_code", "ix_platform_invitations_email"]:
        op.drop_index(name, table_name="platform_invitations")
    op.drop_table("platform_invitations")
    op.alter_column("evaluation_runs", "tenant_id", existing_type=sa.String(36), nullable=False)
    op.alter_column("audit_events", "tenant_id", existing_type=sa.String(36), nullable=False)
    bind.execute(sa.text(
        "UPDATE auth_sessions SET tenant_id=(SELECT tenant_id FROM tenant_memberships m "
        "WHERE m.user_id=auth_sessions.user_id ORDER BY m.created_at LIMIT 1) WHERE tenant_id IS NULL"
    ))
    op.alter_column("auth_sessions", "tenant_id", existing_type=sa.String(36), nullable=False)
    op.drop_index("ix_auth_sessions_context_type", table_name="auth_sessions")
    op.drop_column("auth_sessions", "context_type")
    bind.execute(sa.text(
        "UPDATE platform_role_bindings SET role_code='platform_admin' WHERE role_code='platform_super_admin'"
    ))
    for role in PLATFORM_ROLES:
        bind.execute(sa.text("DELETE FROM role_permissions WHERE role_code=:role"), {"role": role})
        bind.execute(sa.text("DELETE FROM role_definitions WHERE code=:role"), {"role": role})
    op.drop_index("ix_users_account_type", table_name="users")
    op.drop_column("users", "account_type")
