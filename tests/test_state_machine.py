import pytest

from backend.models import AgentRun, Project
from backend.state_machine import (
    InvalidStateTransition,
    ProjectLifecycle,
    RunLifecycle,
    transition_project,
    transition_run,
)


def test_project_and_run_state_machines_are_independent():
    project = Project(
        tenant_id="tenant",
        name="状态机项目",
        customer_name="客户",
        lifecycle_status="draft",
        version=1,
    )
    run = AgentRun(
        tenant_id="tenant",
        project_id="project",
        run_number=1,
        status="pending",
        version=1,
        trace_id="trace",
    )

    transition_project(project, ProjectLifecycle.IN_PROGRESS)
    transition_run(run, RunLifecycle.RUNNING)
    transition_run(run, RunLifecycle.WAITING_APPROVAL)

    assert project.lifecycle_status == "in_progress"
    assert run.status == "waiting_approval"
    assert project.version == 2
    assert run.version == 3


def test_terminal_run_cannot_be_restarted_in_place():
    run = AgentRun(
        tenant_id="tenant",
        project_id="project",
        run_number=1,
        status="failed",
        version=2,
        trace_id="trace",
    )

    with pytest.raises(InvalidStateTransition):
        transition_run(run, RunLifecycle.RUNNING)
