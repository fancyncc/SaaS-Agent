"""database-backed V2.1 permission model

Revision ID: 0005_v2_permissions
Revises: 0004_remove_waiting_input
Create Date: 2026-08-25
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

revision = "0005_v2_permissions"
down_revision = "0004_remove_waiting_input"
branch_labels = None
depends_on = None


ROLES = {
    "platform_admin": ("platform", "平台管理员", "平台治理，不直接继承客户项目操作权限"),
    "company_admin": ("company", "公司管理员", "管理本公司成员、项目归属和协作"),
    "company_member": ("company", "公司成员", "通过项目成员关系获得业务权限"),
    "project_manager": ("project", "项目经理", "项目治理、成员和执行管理"),
    "implementation_consultant": ("project", "实施顾问", "需求实施、Agent 执行和数据导入"),
    "approver": ("project", "审批人", "独立审批和验收决策"),
    "customer_contact": ("project", "客户联系人", "需求确认、反馈和验收提交"),
    "viewer": ("project", "只读成员", "项目只读观察"),
}

PERMISSIONS = {
    "company.create": ("company", "create", "创建公司", "high"),
    "company.view": ("company", "view", "查看公司", "low"),
    "company.update": ("company", "update", "更新公司", "high"),
    "member.view": ("member", "view", "查看公司成员", "low"),
    "member.invite": ("member", "invite", "邀请公司成员", "medium"),
    "member.role.assign": ("member", "role.assign", "分配公司身份", "high"),
    "member.disable": ("member", "disable", "停用公司成员", "high"),
    "project.view": ("project", "view", "查看项目", "low"),
    "project.create": ("project", "create", "创建项目", "medium"),
    "project.edit": ("project", "edit", "编辑项目", "medium"),
    "project.delete": ("project", "delete", "软删除项目", "high"),
    "project.restore": ("project", "restore", "恢复项目", "high"),
    "project.member.view": ("project.member", "view", "查看项目成员", "low"),
    "project.member.assign": ("project.member", "assign", "分配项目主角色", "high"),
    "project.member.revoke": ("project.member", "revoke", "撤销项目成员", "high"),
    "collaboration.view": ("collaboration", "view", "查看协作公司", "low"),
    "collaboration.invite": ("collaboration", "invite", "邀请协作公司", "high"),
    "collaboration.accept": ("collaboration", "accept", "接受协作", "high"),
    "collaboration.reject": ("collaboration", "reject", "拒绝协作", "medium"),
    "collaboration.revoke": ("collaboration", "revoke", "撤销协作", "high"),
    "run.view": ("run", "view", "查看 Agent Run", "low"),
    "run.start": ("run", "start", "启动 Agent", "medium"),
    "run.cancel": ("run", "cancel", "取消 Agent Run", "medium"),
    "run.retry": ("run", "retry", "重试 Agent Run", "medium"),
    "approval.view": ("approval", "view", "查看审批", "low"),
    "approval.decide": ("approval", "decide", "处理审批", "high"),
    "import.view": ("import", "view", "查看导入结果", "low"),
    "import.validate": ("import", "validate", "校验导入", "medium"),
    "import.submit": ("import", "submit", "提交导入审批", "medium"),
    "import.execute": ("import", "execute", "执行已审批导入", "high"),
    "acceptance.view": ("acceptance", "view", "查看验收", "low"),
    "acceptance.submit": ("acceptance", "submit", "提交验收", "medium"),
    "acceptance.decide": ("acceptance", "decide", "决定验收", "high"),
    "artifact.view": ("artifact", "view", "查看交付物", "low"),
    "artifact.create": ("artifact", "create", "生成交付物", "medium"),
    "audit.view": ("audit", "view", "查看审计", "medium"),
    "audit.export": ("audit", "export", "导出审计", "high"),
    "system.view": ("system", "view", "查看系统状态", "low"),
    "support.request": ("support", "request", "申请支持访问", "medium"),
    "support.approve": ("support", "approve", "批准支持访问", "high"),
    "support.revoke": ("support", "revoke", "撤销支持访问", "high"),
}

ROLE_PERMISSIONS = {
    "platform_admin": {
        "company.create", "company.view", "company.update", "audit.view", "audit.export",
        "system.view", "support.request", "support.revoke",
    },
    "company_admin": {
        "company.view", "company.update", "member.view", "member.invite", "member.role.assign",
        "member.disable", "project.view", "project.create", "project.delete", "project.restore",
        "project.member.view", "project.member.assign", "project.member.revoke",
        "collaboration.view", "collaboration.invite", "collaboration.accept",
        "collaboration.reject", "collaboration.revoke", "audit.view", "support.approve",
        "support.revoke",
    },
    "company_member": set(),
    "project_manager": {
        "project.view", "project.edit", "project.member.view", "project.member.assign",
        "project.member.revoke", "collaboration.view", "run.view", "run.start", "run.cancel",
        "run.retry", "approval.view", "import.view", "acceptance.view", "artifact.view", "audit.view",
    },
    "implementation_consultant": {
        "project.view", "project.edit", "project.member.view", "run.view", "run.start", "run.cancel",
        "run.retry", "approval.view", "import.view", "import.validate", "import.submit",
        "import.execute", "acceptance.view", "artifact.view", "artifact.create", "audit.view",
    },
    "approver": {
        "project.view", "run.view", "approval.view", "approval.decide", "import.view",
        "acceptance.view", "acceptance.decide", "artifact.view", "audit.view",
    },
    "customer_contact": {
        "project.view", "run.view", "approval.view", "acceptance.view", "acceptance.submit",
        "artifact.view",
    },
    "viewer": {
        "project.view", "run.view", "approval.view", "import.view", "acceptance.view", "artifact.view",
    },
}


def _seed(bind: sa.Connection) -> None:
    role_table = sa.table(
        "role_definitions", sa.column("id"), sa.column("code"), sa.column("scope"),
        sa.column("name"), sa.column("description"), sa.column("built_in"), sa.column("status"),
        sa.column("created_at"),
    )
    permission_table = sa.table(
        "permission_definitions", sa.column("id"), sa.column("code"), sa.column("resource"),
        sa.column("action"), sa.column("description"), sa.column("risk_level"),
    )
    op.bulk_insert(role_table, [
        {"id": str(uuid4()), "code": code, "scope": scope, "name": name,
         "description": description, "built_in": True, "status": "active",
         "created_at": datetime.now(UTC)}
        for code, (scope, name, description) in ROLES.items()
    ])
    op.bulk_insert(permission_table, [
        {"id": str(uuid4()), "code": code, "resource": resource, "action": action,
         "description": description, "risk_level": risk}
        for code, (resource, action, description, risk) in PERMISSIONS.items()
    ])
    insert = sa.text(
        "INSERT INTO role_permissions (role_code, permission_code) "
        "VALUES (:role_code, :permission_code)"
    )
    for role_code, permission_codes in ROLE_PERMISSIONS.items():
        for permission_code in permission_codes:
            bind.execute(insert, {"role_code": role_code, "permission_code": permission_code})


def _repair_legacy_schema() -> None:
    """Make the physical schema match the ORM after the early create_all migrations."""
    op.create_index("ix_agent_runs_project_id", "agent_runs", ["project_id"])
    op.create_foreign_key("fk_agent_runs_tenant_id", "agent_runs", "customer_tenants", ["tenant_id"], ["id"])
    op.create_index("ix_agent_steps_run_id", "agent_steps", ["run_id"])
    op.create_foreign_key("fk_agent_steps_tenant_id", "agent_steps", "customer_tenants", ["tenant_id"], ["id"])
    op.create_index("ix_approval_requests_run_id", "approval_requests", ["run_id"])
    op.create_foreign_key("fk_approval_requests_tenant_id", "approval_requests", "customer_tenants", ["tenant_id"], ["id"])

    op.alter_column("audit_events", "actor", existing_type=sa.String(80), type_=sa.String(160))
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_resource_id", "audit_events", ["resource_id"])
    op.create_foreign_key("fk_audit_events_actor_user_id", "audit_events", "users", ["actor_user_id"], ["id"])
    op.create_foreign_key("fk_audit_events_tenant_id", "audit_events", "customer_tenants", ["tenant_id"], ["id"])
    op.create_foreign_key("fk_evaluation_runs_tenant_id", "evaluation_runs", "customer_tenants", ["tenant_id"], ["id"])
    op.create_foreign_key("fk_idempotency_records_tenant_id", "idempotency_records", "customer_tenants", ["tenant_id"], ["id"])

    op.create_index("ix_implementation_projects_deleted_at", "implementation_projects", ["deleted_at"])
    op.create_index("ix_implementation_projects_status", "implementation_projects", ["status"])
    op.create_foreign_key("fk_projects_tenant_id", "implementation_projects", "customer_tenants", ["tenant_id"], ["id"])
    op.create_foreign_key("fk_projects_created_by", "implementation_projects", "users", ["created_by"], ["id"])
    op.create_foreign_key("fk_projects_deleted_by", "implementation_projects", "users", ["deleted_by"], ["id"])
    op.create_foreign_key("fk_projects_owner_user_id", "implementation_projects", "users", ["owner_user_id"], ["id"])
    op.create_index("ix_import_jobs_project_id", "import_jobs", ["project_id"])
    op.create_foreign_key("fk_import_jobs_tenant_id", "import_jobs", "customer_tenants", ["tenant_id"], ["id"])
    op.create_foreign_key("fk_project_documents_tenant_id", "project_documents", "customer_tenants", ["tenant_id"], ["id"])
    op.create_index("ix_project_memberships_project_role", "project_memberships", ["project_role"])

    op.drop_index("ix_project_collaborations_project_id", table_name="project_company_collaborations")
    op.drop_index("ix_project_collaborations_tenant_id", table_name="project_company_collaborations")
    op.drop_index("ix_project_collaborations_status", table_name="project_company_collaborations")
    op.create_index("ix_project_company_collaborations_owner_tenant_id", "project_company_collaborations", ["owner_tenant_id"])
    op.create_index("ix_project_company_collaborations_project_id", "project_company_collaborations", ["project_id"])
    op.create_index("ix_project_company_collaborations_tenant_id", "project_company_collaborations", ["tenant_id"])
    op.create_index("ix_project_company_collaborations_status", "project_company_collaborations", ["status"])


def _apply_v21_rls() -> None:
    project_scope = """
      (({alias}.tenant_id = current_setting('app.current_tenant_id', true)
        OR EXISTS (SELECT 1 FROM project_company_collaborations pc
          WHERE pc.project_id={alias}.id
            AND pc.tenant_id=current_setting('app.current_tenant_id', true)
            AND pc.status='active'))
       AND (current_setting('app.current_company_role', true)='company_admin'
        OR EXISTS (SELECT 1 FROM project_memberships pm
          WHERE pm.project_id={alias}.id
            AND pm.user_id=current_setting('app.current_user_id', true)
            AND pm.status='active')))
    """
    project_scope_root = project_scope.format(alias="implementation_projects")
    op.execute("ALTER TABLE implementation_projects ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS project_tenant_policy ON implementation_projects")
    op.execute("DROP POLICY IF EXISTS project_scope_policy ON implementation_projects")
    op.execute("DROP POLICY IF EXISTS project_write_policy ON implementation_projects")
    op.execute(f"""
        CREATE POLICY project_scope_policy ON implementation_projects FOR SELECT USING (
          {project_scope_root} OR EXISTS (
            SELECT 1 FROM support_access_grants sag
            JOIN support_access_grant_permissions sagp ON sagp.grant_id=sag.id
            WHERE sag.requester_user_id=current_setting('app.current_user_id', true)
              AND sag.target_project_id=implementation_projects.id
              AND sag.status='approved' AND sag.revoked_at IS NULL
              AND sag.expires_at > CURRENT_TIMESTAMP
              AND sagp.permission_code='project.view'
          )
        )
    """)
    op.execute(f"""
        CREATE POLICY project_write_policy ON implementation_projects FOR ALL
        USING ({project_scope_root})
        WITH CHECK ({project_scope_root})
    """)

    direct_children = {
        "project_documents": "project_id",
        "agent_runs": "project_id",
        "import_jobs": "project_id",
    }
    project_scope_p = project_scope.format(alias="p")
    for table, project_column in direct_children.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        for policy in ("project_policy", "read_policy", "write_policy"):
            op.execute(f"DROP POLICY IF EXISTS {table}_{policy} ON {table}")
        op.execute(f"""
            CREATE POLICY {table}_read_policy ON {table} FOR SELECT USING (
              EXISTS (SELECT 1 FROM implementation_projects p
                WHERE p.id={table}.{project_column})
            )
        """)
        op.execute(f"""
            CREATE POLICY {table}_write_policy ON {table} FOR ALL
            USING (EXISTS (SELECT 1 FROM implementation_projects p
              WHERE p.id={table}.{project_column} AND {project_scope_p}))
            WITH CHECK (EXISTS (SELECT 1 FROM implementation_projects p
              WHERE p.id={table}.{project_column} AND {project_scope_p}))
        """)

    for table in ("agent_steps", "approval_requests"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        for policy in ("run_policy", "read_policy", "write_policy"):
            op.execute(f"DROP POLICY IF EXISTS {table}_{policy} ON {table}")
        op.execute(f"""
            CREATE POLICY {table}_read_policy ON {table} FOR SELECT USING (
              EXISTS (SELECT 1 FROM agent_runs r WHERE r.id={table}.run_id)
            )
        """)
        op.execute(f"""
            CREATE POLICY {table}_write_policy ON {table} FOR ALL
            USING (EXISTS (SELECT 1 FROM agent_runs r
              JOIN implementation_projects p ON p.id=r.project_id
              WHERE r.id={table}.run_id AND {project_scope_p}))
            WITH CHECK (EXISTS (SELECT 1 FROM agent_runs r
              JOIN implementation_projects p ON p.id=r.project_id
              WHERE r.id={table}.run_id AND {project_scope_p}))
        """)

    op.execute("DROP VIEW IF EXISTS support_project_directory")
    op.execute("""
        CREATE VIEW support_project_directory WITH (security_barrier=true) AS
        SELECT id, tenant_id, name, customer_name
        FROM implementation_projects WHERE deleted_at IS NULL
    """)


def _upgrade_precreated_fresh_schema(bind: sa.Connection) -> bool:
    """Handle empty installs where legacy 0001 created the current ORM schema."""
    if "role_definitions" not in set(sa.inspect(bind).get_table_names()):
        return False
    if not bind.execute(sa.text("SELECT 1 FROM role_definitions LIMIT 1")).first():
        _seed(bind)
    bind.execute(sa.text(
        "UPDATE tenant_memberships SET company_role_code = CASE role "
        "WHEN 'tenant_admin' THEN 'company_admin' ELSE 'company_member' END"
    ))
    bind.execute(sa.text("UPDATE project_memberships SET primary_role_code = project_role"))
    bind.execute(sa.text("""
        INSERT INTO platform_role_bindings
          (id,user_id,role_code,status,granted_by,created_at)
        SELECT CAST(gen_random_uuid() AS varchar),u.id,'platform_admin','active',u.id,CURRENT_TIMESTAMP
        FROM users u WHERE u.is_platform_admin=true
          AND NOT EXISTS (SELECT 1 FROM platform_role_bindings b
            WHERE b.user_id=u.id AND b.role_code='platform_admin')
    """))
    if bind.dialect.name == "postgresql":
        _apply_v21_rls()
    return True


def upgrade() -> None:
    bind = op.get_bind()
    unknown_company = bind.execute(sa.text(
        "SELECT DISTINCT role FROM tenant_memberships "
        "WHERE role NOT IN ('tenant_admin','tenant_member')"
    )).scalars().all()
    unknown_project = bind.execute(sa.text(
        "SELECT DISTINCT project_role FROM project_memberships WHERE project_role NOT IN "
        "('project_manager','implementation_consultant','approver','customer_contact','viewer')"
    )).scalars().all()
    duplicates = bind.execute(sa.text(
        "SELECT user_id FROM tenant_memberships WHERE status='active' "
        "GROUP BY user_id HAVING COUNT(*) > 1"
    )).scalars().all()
    if unknown_company or unknown_project or duplicates:
        raise RuntimeError(
            f"V2 permission preflight failed: company_roles={unknown_company}, "
            f"project_roles={unknown_project}, duplicate_company_users={duplicates}"
        )

    if _upgrade_precreated_fresh_schema(bind):
        return

    op.create_table(
        "role_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("built_in", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("scope IN ('platform','company','project')", name="ck_role_scope"),
    )
    op.create_index("ix_role_definitions_code", "role_definitions", ["code"], unique=True)
    op.create_index("ix_role_definitions_scope", "role_definitions", ["scope"])
    op.create_index("ix_role_definitions_status", "role_definitions", ["status"])
    op.create_table(
        "permission_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("resource", sa.String(60), nullable=False),
        sa.Column("action", sa.String(60), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("risk_level", sa.String(20), nullable=False, server_default="low"),
    )
    op.create_index("ix_permission_definitions_code", "permission_definitions", ["code"], unique=True)
    op.create_index("ix_permission_definitions_resource", "permission_definitions", ["resource"])
    op.create_table(
        "role_permissions",
        sa.Column("role_code", sa.String(80), sa.ForeignKey("role_definitions.code"), primary_key=True),
        sa.Column("permission_code", sa.String(100), sa.ForeignKey("permission_definitions.code"), primary_key=True),
    )
    _seed(bind)

    op.add_column("tenant_memberships", sa.Column("company_role_code", sa.String(80), nullable=True))
    op.add_column("project_memberships", sa.Column("primary_role_code", sa.String(80), nullable=True))
    op.add_column("agent_runs", sa.Column("started_by", sa.String(36), nullable=True))
    op.create_foreign_key("fk_agent_runs_started_by", "agent_runs", "users", ["started_by"], ["id"])
    op.create_index("ix_agent_runs_started_by", "agent_runs", ["started_by"])
    bind.execute(sa.text(
        "UPDATE tenant_memberships SET company_role_code = CASE role "
        "WHEN 'tenant_admin' THEN 'company_admin' ELSE 'company_member' END"
    ))
    bind.execute(sa.text("UPDATE project_memberships SET primary_role_code = project_role"))
    op.alter_column("tenant_memberships", "company_role_code", nullable=False)
    op.alter_column("project_memberships", "primary_role_code", nullable=False)
    op.create_foreign_key("fk_tenant_membership_company_role", "tenant_memberships", "role_definitions", ["company_role_code"], ["code"])
    op.create_foreign_key("fk_project_membership_primary_role", "project_memberships", "role_definitions", ["primary_role_code"], ["code"])
    op.create_index("ix_tenant_memberships_company_role_code", "tenant_memberships", ["company_role_code"])
    op.create_index("ix_project_memberships_primary_role_code", "project_memberships", ["primary_role_code"])

    op.create_table(
        "platform_role_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role_code", sa.String(80), sa.ForeignKey("role_definitions.code"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("granted_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "role_code", name="uq_platform_role_binding"),
    )
    op.create_index("ix_platform_role_bindings_user_id", "platform_role_bindings", ["user_id"])
    op.create_index("ix_platform_role_bindings_role_code", "platform_role_bindings", ["role_code"])
    op.create_index("ix_platform_role_bindings_status", "platform_role_bindings", ["status"])
    platform_users = bind.execute(sa.text("SELECT id FROM users WHERE is_platform_admin = true")).scalars()
    for user_id in platform_users:
        bind.execute(sa.text(
            "INSERT INTO platform_role_bindings (id,user_id,role_code,status,granted_by,created_at) "
            "VALUES (:id,:user_id,'platform_admin','active',:user_id,CURRENT_TIMESTAMP)"
        ), {"id": str(uuid4()), "user_id": user_id})

    op.create_table(
        "project_capability_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("membership_id", sa.String(36), sa.ForeignKey("project_memberships.id"), nullable=False),
        sa.Column("permission_code", sa.String(100), sa.ForeignKey("permission_definitions.code"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("granted_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("expires_at > granted_at", name="ck_capability_expiry"),
    )
    op.create_index("uq_active_project_capability", "project_capability_grants", ["membership_id", "permission_code"], unique=True, postgresql_where=sa.text("revoked_at IS NULL"))
    op.create_index("ix_project_capability_grants_membership_id", "project_capability_grants", ["membership_id"])
    op.create_index("ix_project_capability_grants_permission_code", "project_capability_grants", ["permission_code"])
    op.create_index("ix_project_capability_grants_expires_at", "project_capability_grants", ["expires_at"])

    op.create_table(
        "support_access_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("requester_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("target_tenant_id", sa.String(36), sa.ForeignKey("customer_tenants.id"), nullable=False),
        sa.Column("target_project_id", sa.String(36), sa.ForeignKey("implementation_projects.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("expires_at > requested_at", name="ck_support_expiry"),
    )
    op.create_index("ix_support_access_grants_requester_user_id", "support_access_grants", ["requester_user_id"])
    op.create_index("ix_support_access_grants_target_tenant_id", "support_access_grants", ["target_tenant_id"])
    op.create_index("ix_support_access_grants_target_project_id", "support_access_grants", ["target_project_id"])
    op.create_index("ix_support_access_grants_status", "support_access_grants", ["status"])
    op.create_index("ix_support_access_grants_expires_at", "support_access_grants", ["expires_at"])
    op.create_table(
        "support_access_grant_permissions",
        sa.Column("grant_id", sa.String(36), sa.ForeignKey("support_access_grants.id"), primary_key=True),
        sa.Column("permission_code", sa.String(100), sa.ForeignKey("permission_definitions.code"), primary_key=True),
    )

    if bind.dialect.name == "postgresql":
        _repair_legacy_schema()
        indexes = {index["name"] for index in sa.inspect(bind).get_indexes("tenant_memberships")}
        if "uq_active_company_per_user" not in indexes:
            op.create_index("uq_active_company_per_user", "tenant_memberships", ["user_id"], unique=True, postgresql_where=sa.text("status='active'"))
        _apply_v21_rls()
        return
        op.execute("DROP POLICY IF EXISTS project_tenant_policy ON implementation_projects")
        op.execute("DROP POLICY IF EXISTS project_scope_policy ON implementation_projects")
        op.execute("DROP POLICY IF EXISTS project_write_policy ON implementation_projects")
        op.execute("ALTER TABLE implementation_projects ENABLE ROW LEVEL SECURITY")
        op.execute("""
            CREATE POLICY project_scope_policy ON implementation_projects FOR SELECT USING (
              (
                (tenant_id = current_setting('app.current_tenant_id', true)
                 OR EXISTS (SELECT 1 FROM project_company_collaborations pc
                   WHERE pc.project_id = implementation_projects.id
                     AND pc.tenant_id = current_setting('app.current_tenant_id', true)
                     AND pc.status = 'active'))
                AND (
                  current_setting('app.current_company_role', true) = 'company_admin'
                  OR EXISTS (SELECT 1 FROM project_memberships pm
                    WHERE pm.project_id = implementation_projects.id
                      AND pm.user_id = current_setting('app.current_user_id', true)
                      AND pm.status = 'active')
                )
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
        op.execute("""
            CREATE POLICY project_write_policy ON implementation_projects FOR ALL
            USING (
              (tenant_id = current_setting('app.current_tenant_id', true)
               OR EXISTS (SELECT 1 FROM project_company_collaborations pc
                 WHERE pc.project_id = implementation_projects.id
                   AND pc.tenant_id = current_setting('app.current_tenant_id', true)
                   AND pc.status = 'active'))
              AND (
                current_setting('app.current_company_role', true) = 'company_admin'
                OR EXISTS (SELECT 1 FROM project_memberships pm
                  WHERE pm.project_id = implementation_projects.id
                    AND pm.user_id = current_setting('app.current_user_id', true)
                    AND pm.status = 'active')
              )
            )
            WITH CHECK (
              tenant_id = current_setting('app.current_tenant_id', true)
              OR EXISTS (SELECT 1 FROM project_company_collaborations pc
                WHERE pc.project_id = implementation_projects.id
                  AND pc.tenant_id = current_setting('app.current_tenant_id', true)
                  AND pc.status = 'active')
            )
        """)
        direct_children = {
            "project_documents": "project_id",
            "agent_runs": "project_id",
            "import_jobs": "project_id",
        }
        for table, project_column in direct_children.items():
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"DROP POLICY IF EXISTS {table}_project_policy ON {table}")
            op.execute(f"DROP POLICY IF EXISTS {table}_read_policy ON {table}")
            op.execute(f"DROP POLICY IF EXISTS {table}_write_policy ON {table}")
            op.execute(f"""
                CREATE POLICY {table}_read_policy ON {table} FOR SELECT USING (
                  EXISTS (SELECT 1 FROM implementation_projects p
                    WHERE p.id = {table}.{project_column})
                )
            """)
            op.execute(f"""
                CREATE POLICY {table}_write_policy ON {table} FOR ALL
                USING (
                  EXISTS (SELECT 1 FROM implementation_projects p
                    WHERE p.id = {table}.{project_column}
                      AND (p.tenant_id = current_setting('app.current_tenant_id', true)
                        OR EXISTS (SELECT 1 FROM project_company_collaborations pc
                          WHERE pc.project_id=p.id
                            AND pc.tenant_id=current_setting('app.current_tenant_id', true)
                            AND pc.status='active'))
                      AND (current_setting('app.current_company_role', true)='company_admin'
                        OR EXISTS (SELECT 1 FROM project_memberships pm
                          WHERE pm.project_id=p.id
                            AND pm.user_id=current_setting('app.current_user_id', true)
                            AND pm.status='active')))
                )
                WITH CHECK (
                  EXISTS (SELECT 1 FROM implementation_projects p
                    WHERE p.id = {table}.{project_column}
                      AND (p.tenant_id = current_setting('app.current_tenant_id', true)
                        OR EXISTS (SELECT 1 FROM project_company_collaborations pc
                          WHERE pc.project_id=p.id
                            AND pc.tenant_id=current_setting('app.current_tenant_id', true)
                            AND pc.status='active'))
                      AND (current_setting('app.current_company_role', true)='company_admin'
                        OR EXISTS (SELECT 1 FROM project_memberships pm
                          WHERE pm.project_id=p.id
                            AND pm.user_id=current_setting('app.current_user_id', true)
                            AND pm.status='active')))
                )
            """)
        for table in ("agent_steps", "approval_requests"):
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"DROP POLICY IF EXISTS {table}_run_policy ON {table}")
            op.execute(f"DROP POLICY IF EXISTS {table}_read_policy ON {table}")
            op.execute(f"DROP POLICY IF EXISTS {table}_write_policy ON {table}")
            op.execute(f"""
                CREATE POLICY {table}_read_policy ON {table} FOR SELECT USING (
                  EXISTS (SELECT 1 FROM agent_runs r WHERE r.id={table}.run_id)
                )
            """)
            op.execute(f"""
                CREATE POLICY {table}_write_policy ON {table} FOR ALL
                USING (
                  EXISTS (SELECT 1 FROM agent_runs r
                    JOIN implementation_projects p ON p.id=r.project_id
                    WHERE r.id={table}.run_id
                      AND (p.tenant_id=current_setting('app.current_tenant_id', true)
                        OR EXISTS (SELECT 1 FROM project_company_collaborations pc
                          WHERE pc.project_id=p.id
                            AND pc.tenant_id=current_setting('app.current_tenant_id', true)
                            AND pc.status='active'))
                      AND (current_setting('app.current_company_role', true)='company_admin'
                        OR EXISTS (SELECT 1 FROM project_memberships pm
                          WHERE pm.project_id=p.id
                            AND pm.user_id=current_setting('app.current_user_id', true)
                            AND pm.status='active')))
                )
                WITH CHECK (
                  EXISTS (SELECT 1 FROM agent_runs r
                    JOIN implementation_projects p ON p.id=r.project_id
                    WHERE r.id={table}.run_id
                      AND (p.tenant_id=current_setting('app.current_tenant_id', true)
                        OR EXISTS (SELECT 1 FROM project_company_collaborations pc
                          WHERE pc.project_id=p.id
                            AND pc.tenant_id=current_setting('app.current_tenant_id', true)
                            AND pc.status='active'))
                      AND (current_setting('app.current_company_role', true)='company_admin'
                        OR EXISTS (SELECT 1 FROM project_memberships pm
                          WHERE pm.project_id=p.id
                            AND pm.user_id=current_setting('app.current_user_id', true)
                            AND pm.status='active')))
                )
            """)
        op.execute("DROP VIEW IF EXISTS support_project_directory")
        op.execute("""
            CREATE VIEW support_project_directory WITH (security_barrier=true) AS
            SELECT id, tenant_id, name, customer_name
            FROM implementation_projects WHERE deleted_at IS NULL
        """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS project_scope_policy ON implementation_projects")
        op.execute("DROP POLICY IF EXISTS project_write_policy ON implementation_projects")
        op.execute("DROP VIEW IF EXISTS support_project_directory")
    op.drop_table("support_access_grant_permissions")
    op.drop_table("support_access_grants")
    op.drop_index("uq_active_project_capability", table_name="project_capability_grants")
    op.drop_table("project_capability_grants")
    op.drop_table("platform_role_bindings")
    op.drop_index("ix_agent_runs_started_by", table_name="agent_runs")
    op.drop_constraint("fk_agent_runs_started_by", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "started_by")
    op.drop_index("ix_project_memberships_primary_role_code", table_name="project_memberships")
    op.drop_constraint("fk_project_membership_primary_role", "project_memberships", type_="foreignkey")
    op.drop_column("project_memberships", "primary_role_code")
    op.drop_index("ix_tenant_memberships_company_role_code", table_name="tenant_memberships")
    op.drop_constraint("fk_tenant_membership_company_role", "tenant_memberships", type_="foreignkey")
    op.drop_column("tenant_memberships", "company_role_code")
    op.drop_table("role_permissions")
    op.drop_table("permission_definitions")
    op.drop_table("role_definitions")
