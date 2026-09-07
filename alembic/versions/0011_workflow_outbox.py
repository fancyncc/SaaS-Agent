"""Transactional workflow outbox.

Revision ID: 0011_workflow_outbox
Revises: 0010_delivery_core
"""
import sqlalchemy as sa

from alembic import op

revision = "0011_workflow_outbox"
down_revision = "0010_delivery_core"
branch_labels = None
depends_on = None


def upgrade():
    if "workflow_outbox" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table("workflow_outbox",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("run_id", sa.String(36), sa.ForeignKey("agent_runs.id"), nullable=False),
            sa.Column("run_version", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.String(36), sa.ForeignKey("customer_tenants.id"), nullable=False),
            sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("processed", sa.Boolean(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("last_error", sa.Text(), nullable=False),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("run_id", "run_version"))
        for column in ("run_id", "tenant_id", "processed"):
            op.create_index(f"ix_workflow_outbox_{column}", "workflow_outbox", [column])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM workflow_outbox WHERE NOT processed")):
        raise RuntimeError("Drain outbox before downgrading")
    op.drop_table("workflow_outbox")
