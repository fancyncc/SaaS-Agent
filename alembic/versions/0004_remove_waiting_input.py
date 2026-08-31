"""remove obsolete waiting-for-input workflow state

Revision ID: 0004_remove_waiting_input
Revises: 0003_membership_uuid
Create Date: 2026-08-25
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004_remove_waiting_input"
down_revision = "0003_membership_uuid"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    update_run = sa.text(
        "UPDATE agent_runs SET status = 'failed', state = :state, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = :run_id"
    ).bindparams(sa.bindparam("state", type_=sa.JSON()))
    legacy_runs = bind.execute(
        sa.text("SELECT id, state FROM agent_runs WHERE status = 'waiting_input'")
    ).mappings()
    for row in legacy_runs:
        state = dict(row["state"] or {})
        state["status"] = "failed"
        state.pop("missing_information", None)
        state["legacy_notice"] = "旧版资料补充流程已移除，请重新启动 Agent"
        bind.execute(update_run, {"state": state, "run_id": row["id"]})

    bind.execute(
        sa.text(
            "UPDATE implementation_projects SET status = 'failed' "
            "WHERE status = 'waiting_input'"
        )
    )


def downgrade() -> None:
    # The removed state had no resume endpoint, so restoring it would recreate stuck runs.
    pass
