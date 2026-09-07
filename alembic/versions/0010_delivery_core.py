"""Persist delivery effects and material-bound imports.

Revision ID: 0010_delivery_core
Revises: 0009_company_admin_read
"""
import sqlalchemy as sa

from alembic import op

revision = "0010_delivery_core"
down_revision = "0009_company_admin_read"
branch_labels = None
depends_on = None


def identity():
    return [sa.Column("id", sa.String(36), primary_key=True), sa.Column("tenant_id", sa.String(36), sa.ForeignKey("customer_tenants.id"), nullable=False)]


def project_columns():
    return [*identity(), sa.Column("project_id", sa.String(36), sa.ForeignKey("implementation_projects.id"), nullable=False)]


def run_columns():
    return [*project_columns(), sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=False)]


def upgrade():
    # Historical 0001 creates current metadata for empty installations.
    if "tool_executions" in sa.inspect(op.get_bind()).get_table_names():
        policies()
        return
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("ck_agent_run_status", type_="check")
        batch.create_check_constraint("ck_agent_run_status", "status IN ('pending','running','preparing_materials','waiting_approval','succeeded','failed','cancelled')")
    with op.batch_alter_table("import_jobs") as batch:
        batch.add_column(sa.Column("run_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_import_run", "agent_runs", ["run_id"], ["id"])
        batch.create_index("ix_import_jobs_run_id", ["run_id"])
    op.create_table("saas_workspaces", *project_columns(), sa.Column("configuration", sa.JSON(), nullable=False), sa.Column("version", sa.Integer(), nullable=False), sa.UniqueConstraint("project_id"))
    op.create_table("saas_members", *identity(), sa.Column("workspace_id", sa.String(36), sa.ForeignKey("saas_workspaces.id"), nullable=False), sa.Column("name", sa.String(120), nullable=False), sa.Column("email", sa.String(160), nullable=False), sa.Column("department", sa.String(120), nullable=False), sa.Column("role", sa.String(40), nullable=False), sa.UniqueConstraint("workspace_id", "email"))
    op.create_table("tool_executions", *run_columns(), sa.Column("kind", sa.String(40), nullable=False), sa.Column("material_hash", sa.String(64), nullable=False), sa.Column("before", sa.JSON(), nullable=False), sa.Column("result", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("project_id", "kind", "material_hash"))
    op.create_table("project_artifacts", *run_columns(), sa.Column("kind", sa.String(60), nullable=False), sa.Column("title", sa.String(160), nullable=False), sa.Column("version", sa.Integer(), nullable=False), sa.Column("checksum", sa.String(64), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("run_id", "kind", "checksum"))
    op.create_table("project_feedback", *run_columns(), sa.Column("author", sa.String(36), sa.ForeignKey("users.id"), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    for table in ("saas_workspaces", "saas_members", "tool_executions", "project_artifacts", "project_feedback"):
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        if table not in {"saas_workspaces", "saas_members"}:
            op.create_index(f"ix_{table}_project_id", table, ["project_id"])
            op.create_index(f"ix_{table}_run_id", table, ["run_id"])
    op.create_index("ix_saas_members_workspace_id", "saas_members", ["workspace_id"])
    policies()


def policies():
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in ("saas_workspaces", "saas_members", "tool_executions", "project_artifacts", "project_feedback"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS delivery_scope ON {table}")
        project_match = f"p.id = {table}.project_id" if table != "saas_members" else "p.id IN (SELECT project_id FROM saas_workspaces WHERE id = saas_members.workspace_id)"
        op.execute(f"CREATE POLICY delivery_scope ON {table} USING (EXISTS (SELECT 1 FROM implementation_projects p WHERE {project_match})) WITH CHECK (EXISTS (SELECT 1 FROM implementation_projects p WHERE {project_match}))")


def downgrade():
    count = op.get_bind().scalar(sa.text("SELECT count(*) FROM agent_runs WHERE status = 'preparing_materials'"))
    if count:
        raise RuntimeError("Cancel material-preparation runs before downgrading")
    for table in ("project_feedback", "project_artifacts", "tool_executions", "saas_members", "saas_workspaces"):
        op.drop_table(table)
    foreign_keys = sa.inspect(op.get_bind()).get_foreign_keys("import_jobs")
    constraint = next(fk for fk in foreign_keys if fk["constrained_columns"] == ["run_id"])
    naming = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
    with op.batch_alter_table("import_jobs", naming_convention=naming) as batch:
        batch.drop_index("ix_import_jobs_run_id")
        batch.drop_constraint(constraint["name"] or "fk_import_jobs_run_id_agent_runs", type_="foreignkey")
        batch.drop_column("run_id")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint("ck_agent_run_status", type_="check")
        batch.create_check_constraint("ck_agent_run_status", "status IN ('pending','running','waiting_approval','succeeded','failed','cancelled')")
