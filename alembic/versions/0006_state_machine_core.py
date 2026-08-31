"""separate project and run state machines

Revision ID: 0006_state_machine
Revises: 0005_v2_permissions
Create Date: 2026-08-26
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006_state_machine"
down_revision = "0005_v2_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Revision 0001 creates the current SQLAlchemy metadata on a completely fresh database.
    # In that special path these columns already exist, so this revision only needs to be stamped.
    inspector = sa.inspect(op.get_bind())
    required_columns = {
        "implementation_projects": {"lifecycle_status", "version"},
        "agent_runs": {"run_number", "retry_of_run_id", "version"},
        "agent_steps": {"sequence"},
        "approval_requests": {"version", "decided_at"},
    }
    if all(
        expected.issubset({column["name"] for column in inspector.get_columns(table)})
        for table, expected in required_columns.items()
    ):
        return

    op.add_column(
        "implementation_projects",
        sa.Column("lifecycle_status", sa.String(32), nullable=False, server_default="draft"),
    )
    op.add_column(
        "implementation_projects",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute(
        """
        UPDATE implementation_projects
        SET lifecycle_status = CASE
          WHEN status = 'completed' THEN 'completed'
          WHEN status IN ('active', 'running', 'waiting_approval') THEN 'in_progress'
          WHEN status IN ('rejected', 'failed') THEN 'blocked'
          ELSE 'draft'
        END
        """
    )
    op.create_check_constraint(
        "ck_project_lifecycle_status",
        "implementation_projects",
        "lifecycle_status IN ('draft','ready','in_progress','blocked','completed','cancelled','archived')",
    )
    op.create_index(
        "ix_implementation_projects_lifecycle_status",
        "implementation_projects",
        ["lifecycle_status"],
    )

    op.add_column("agent_runs", sa.Column("run_number", sa.Integer(), nullable=True))
    op.add_column(
        "agent_runs", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column("agent_runs", sa.Column("retry_of_run_id", sa.String(36), nullable=True))
    op.execute(
        """
        WITH numbered AS (
          SELECT id, ROW_NUMBER() OVER (PARTITION BY project_id ORDER BY created_at, id) AS n
          FROM agent_runs
        )
        UPDATE agent_runs AS r SET run_number = numbered.n
        FROM numbered WHERE r.id = numbered.id
        """
    )
    op.execute(
        """
        UPDATE agent_runs
        SET state = jsonb_set(COALESCE(state::jsonb, '{}'::jsonb), '{status}', '"succeeded"'::jsonb, true)::json
        WHERE status = 'completed'
        """
    )
    op.execute("UPDATE agent_runs SET status = 'succeeded' WHERE status = 'completed'")
    op.alter_column("agent_runs", "run_number", nullable=False)
    op.create_foreign_key(
        "fk_agent_runs_retry_of_run_id",
        "agent_runs",
        "agent_runs",
        ["retry_of_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_agent_run_project_number", "agent_runs", ["project_id", "run_number"]
    )
    op.create_check_constraint(
        "ck_agent_run_status",
        "agent_runs",
        "status IN ('pending','running','waiting_approval','succeeded','failed','cancelled')",
    )
    op.create_index("ix_agent_runs_retry_of_run_id", "agent_runs", ["retry_of_run_id"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])

    op.add_column("agent_steps", sa.Column("sequence", sa.Integer(), nullable=True))
    op.execute(
        """
        WITH numbered AS (
          SELECT id, ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY created_at, id) AS n
          FROM agent_steps
        )
        UPDATE agent_steps AS s SET sequence = numbered.n
        FROM numbered WHERE s.id = numbered.id
        """
    )
    op.alter_column("agent_steps", "sequence", nullable=False)
    op.create_unique_constraint(
        "uq_agent_step_run_sequence", "agent_steps", ["run_id", "sequence"]
    )

    op.add_column(
        "approval_requests",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "approval_requests", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        """
        UPDATE approval_requests
        SET version = 2, decided_at = created_at
        WHERE status IN ('approved', 'rejected')
        """
    )
    op.create_check_constraint(
        "ck_approval_status",
        "approval_requests",
        "status IN ('pending','approved','rejected','expired','cancelled')",
    )
    op.create_index(
        "uq_pending_approval_per_run_kind",
        "approval_requests",
        ["run_id", "kind"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.alter_column("implementation_projects", "lifecycle_status", server_default=None)
    op.alter_column("implementation_projects", "version", server_default=None)
    op.alter_column("agent_runs", "version", server_default=None)
    op.alter_column("approval_requests", "version", server_default=None)


def downgrade() -> None:
    op.drop_index("uq_pending_approval_per_run_kind", table_name="approval_requests")
    op.drop_constraint("ck_approval_status", "approval_requests", type_="check")
    op.drop_column("approval_requests", "decided_at")
    op.drop_column("approval_requests", "version")

    op.drop_constraint("uq_agent_step_run_sequence", "agent_steps", type_="unique")
    op.drop_column("agent_steps", "sequence")

    op.drop_index("ix_agent_runs_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_retry_of_run_id", table_name="agent_runs")
    op.drop_constraint("ck_agent_run_status", "agent_runs", type_="check")
    op.drop_constraint("uq_agent_run_project_number", "agent_runs", type_="unique")
    op.drop_constraint("fk_agent_runs_retry_of_run_id", "agent_runs", type_="foreignkey")
    op.execute(
        """
        UPDATE agent_runs
        SET state = jsonb_set(COALESCE(state::jsonb, '{}'::jsonb), '{status}', '"completed"'::jsonb, true)::json
        WHERE status = 'succeeded'
        """
    )
    op.execute("UPDATE agent_runs SET status = 'completed' WHERE status = 'succeeded'")
    op.drop_column("agent_runs", "retry_of_run_id")
    op.drop_column("agent_runs", "version")
    op.drop_column("agent_runs", "run_number")

    op.execute(
        """
        UPDATE implementation_projects
        SET status = CASE
          WHEN lifecycle_status = 'completed' OR lifecycle_status = 'archived' THEN 'completed'
          WHEN lifecycle_status = 'in_progress' THEN 'active'
          WHEN lifecycle_status = 'blocked' OR lifecycle_status = 'cancelled' THEN 'failed'
          ELSE 'draft'
        END
        """
    )
    op.drop_index(
        "ix_implementation_projects_lifecycle_status", table_name="implementation_projects"
    )
    op.drop_constraint(
        "ck_project_lifecycle_status", "implementation_projects", type_="check"
    )
    op.drop_column("implementation_projects", "version")
    op.drop_column("implementation_projects", "lifecycle_status")
