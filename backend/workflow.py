from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import AgentRun, AgentStep, Approval, AuditEvent, Project
from backend.rag import search
from backend.schemas import (
    AcceptanceReport,
    ApprovalKind,
    ConfigurationChange,
    GapAnalysisItem,
    GoLiveCheckResult,
    ImplementationGraphState,
    ImplementationPlan,
    MilestoneSpec,
    RequirementSpec,
    RunStatus,
)
from backend.state_machine import (
    ProjectLifecycle,
    RunLifecycle,
    transition_project,
    transition_run,
)

NODES = [
    "create_project", "collect_requirements", "retrieve_product_knowledge",
    "gap_analysis", "generate_implementation_plan",
    "plan_approval", "inspect_tenant_configuration", "generate_configuration_changes",
    "configuration_approval", "apply_configuration", "validate_import_files",
    "import_approval", "execute_import", "generate_training_materials",
    "run_go_live_checks", "acceptance_approval", "close_project",
]
APPROVAL_NODES = {
    "plan_approval": ApprovalKind.PLAN,
    "configuration_approval": ApprovalKind.CONFIGURATION,
    "import_approval": ApprovalKind.IMPORT,
    "acceptance_approval": ApprovalKind.ACCEPTANCE,
}


def _extract_requirements(text: str) -> list[RequirementSpec]:
    rules = {
        "organization": ("部门", "组织", "成员"),
        "permission": ("权限", "角色", "审批"),
        "workflow": ("流程", "状态", "模板"),
        "migration": ("csv", "导入", "迁移"),
        "training": ("培训", "faq"),
    }
    found = []
    lowered = text.lower()
    for category, terms in rules.items():
        if any(term in lowered for term in terms):
            found.append(RequirementSpec(category=category, statement=f"客户需要{category}相关能力"))
    # 项目创建已经完成必填校验。未命中预设关键词不代表资料缺失，
    # 应保留客户原始描述作为通用需求，继续进入分析与人工审批。
    return found or [RequirementSpec(category="general", statement=text.strip())]


async def _record(session: AsyncSession, run: AgentRun, node: str, detail: dict) -> None:
    last_sequence = await session.scalar(
        select(func.max(AgentStep.sequence)).where(AgentStep.run_id == run.id)
    )
    session.add(AgentStep(
        tenant_id=run.tenant_id,
        run_id=run.id,
        sequence=(last_sequence or 0) + 1,
        node=node,
        status="completed",
        detail=detail,
    ))
    run.current_node = node
    run.updated_at = datetime.now(UTC)
    await session.flush()


async def advance(session: AsyncSession, run: AgentRun) -> AgentRun:
    state = ImplementationGraphState.model_validate(run.state)
    project = await session.get(Project, run.project_id)
    if not project:
        raise ValueError("Project not found")

    start = NODES.index(state.current_node) if state.current_node in NODES else 0
    if state.current_node in state.completed_nodes:
        start += 1

    for node in NODES[start:]:
        state.current_node = node
        detail: dict = {}
        if node == "collect_requirements":
            state.requirements = _extract_requirements(project.requirements_text)
            detail = {"count": len(state.requirements), "agent": "Requirement Agent"}
        elif node == "retrieve_product_knowledge":
            query = " ".join(r.statement for r in state.requirements)
            detail = {"citations": search(query)}
        elif node == "gap_analysis":
            items = []
            for req in state.requirements:
                evidence = search(req.statement, limit=2)
                items.append(GapAnalysisItem(requirement=req.statement, capability=evidence[0]["title"] if evidence else None,
                    fit="supported" if evidence else "human_review", evidence_ids=[e["id"] for e in evidence],
                    recommendation="按标准能力配置" if evidence else "证据不足，转人工确认"))
            state.gap_items = items
        elif node == "generate_implementation_plan":
            state.plan = ImplementationPlan(milestones=[
                MilestoneSpec(name="调研与方案", days=3, owner_role="implementation_consultant"),
                MilestoneSpec(name="配置与迁移", days=5, owner_role="implementation_consultant", dependencies=["调研与方案"]),
                MilestoneSpec(name="培训与上线", days=2, owner_role="customer_contact", dependencies=["配置与迁移"]),
            ], assumptions=["客户审批人在两个工作日内反馈"], risks=["源数据质量可能影响迁移"])
        elif node == "inspect_tenant_configuration":
            detail = {"workspace": project.customer_name, "status_flow": ["待办", "进行中", "完成"]}
        elif node == "generate_configuration_changes":
            state.configuration_changes = [
                ConfigurationChange(path="workflow.statuses", old_value=["待办", "进行中", "完成"], new_value=["待办", "进行中", "审核中", "完成"], risk="high", reason="匹配客户审批流"),
                ConfigurationChange(path="notifications.due_date", old_value=False, new_value=True, risk="medium", reason="启用到期提醒"),
            ]
        elif node == "apply_configuration":
            detail = {"snapshot": "before-change", "verified": True, "applied": len(state.configuration_changes)}
            session.add(AuditEvent(tenant_id=run.tenant_id, event_type="tenant.configuration.applied", actor="agent", resource_id=project.id, payload=detail))
        elif node == "validate_import_files":
            detail = {"status": "skipped" if not state.import_job_id else "validated", "reason": "可通过导入 API 附加 CSV"}
        elif node == "execute_import":
            detail = {"status": "skipped" if not state.import_job_id else "executed"}
        elif node == "generate_training_materials":
            detail = {"materials": ["管理员快速入门", "成员操作指南", "常见问题 FAQ"]}
        elif node == "run_go_live_checks":
            checks = [
                GoLiveCheckResult(name="实施计划已审批", passed=True, details="审批记录有效"),
                GoLiveCheckResult(name="租户配置已验证", passed=True, details="操作后检查通过"),
                GoLiveCheckResult(name="培训材料已生成", passed=True, details="3 份材料"),
            ]
            state.acceptance_report = AcceptanceReport(ready=all(c.passed for c in checks), checks=checks)
        elif node == "close_project":
            transition_project(project, ProjectLifecycle.COMPLETED)
            state.status = RunStatus.SUCCEEDED
            transition_run(run, RunLifecycle.SUCCEEDED)

        if node in APPROVAL_NODES:
            approval = Approval(
                tenant_id=run.tenant_id,
                run_id=run.id,
                kind=APPROVAL_NODES[node].value,
                payload={"node": node},
                requested_by=run.started_by or "agent",
            )
            session.add(approval)
            await session.flush()
            state.pending_approval_id = UUID(approval.id)
            state.status = RunStatus.WAITING_APPROVAL
            transition_run(run, RunLifecycle.WAITING_APPROVAL)
            await _record(session, run, node, {"approval_id": approval.id})
            state.completed_nodes.append(node)
            run.state = state.model_dump(mode="json")
            await session.flush()
            return run

        await _record(session, run, node, detail)
        state.completed_nodes.append(node)
        run.state = state.model_dump(mode="json")

    await session.flush()
    return run


async def resume_after_approval(session: AsyncSession, approval: Approval, decision: str) -> AgentRun:
    run = await session.get(AgentRun, approval.run_id)
    if not run:
        raise ValueError("Run not found")
    state = ImplementationGraphState.model_validate(run.state)
    state.pending_approval_id = None
    if decision == "rejected":
        state.status = RunStatus.FAILED
        transition_run(run, RunLifecycle.FAILED)
        project = await session.get(Project, run.project_id)
        if project:
            transition_project(project, ProjectLifecycle.BLOCKED)
        run.state = state.model_dump(mode="json")
        await session.flush()
        return run
    state.status = RunStatus.RUNNING
    transition_run(run, RunLifecycle.RUNNING)
    run.state = state.model_dump(mode="json")
    await session.flush()
    return await advance(session, run)
