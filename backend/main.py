from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.access import (
    accessible_project_filter,
    accessible_project_or_404,
    project_access,
    require_project_permission,
)
from backend.admin_routes import router as company_router
from backend.audit import audit_event
from backend.auth_routes import router as auth_router
from backend.config import get_settings
from backend.db import bootstrap_identity, get_session, init_db
from backend.imports import validate_member_csv
from backend.models import (
    AgentRun,
    AgentStep,
    Approval,
    IdempotencyRecord,
    ImportJob,
    Project,
    ProjectCollaboration,
    ProjectDocument,
    ProjectMembership,
    Tenant,
)
from backend.platform_routes import router as platform_router
from backend.project_access_routes import router as project_access_router
from backend.schemas import (
    ApprovalDecision,
    Envelope,
    ImplementationGraphState,
    ImportValidateRequest,
    ProjectCreate,
    RunStatus,
)
from backend.security import (
    CSRF_COOKIE,
    Principal,
    current_principal,
    idempotency_key,
    require_company_admin,
)
from backend.state_machine import (
    ProjectLifecycle,
    RunLifecycle,
    transition_project,
    transition_run,
)
from backend.support_routes import platform_router as platform_support_router
from backend.support_routes import router as support_router
from backend.workflow import advance, resume_after_approval


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    await bootstrap_identity()
    yield


app = FastAPI(title=get_settings().app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins.split(","), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def request_security(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-ID", secrets.token_hex(8))
    request.state.trace_id = request.headers.get("traceparent", secrets.token_hex(16))[-32:]
    exempt = {"/api/auth/login", "/api/auth/platform/login", "/api/auth/password/forgot", "/api/auth/password/reset"}
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path.startswith("/api/"):
        invitation_path = (request.url.path.startswith("/api/auth/invitations/")
                           or request.url.path.startswith("/api/auth/platform-invitations/"))
        if request.url.path not in exempt and not invitation_path:
            cookie, header = request.cookies.get(CSRF_COOKIE), request.headers.get("X-CSRF-Token")
            if not cookie or not header or not secrets.compare_digest(cookie, header):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "CSRF 校验失败"},
                    headers={
                        "X-Request-ID": request.state.request_id,
                        "X-Trace-ID": request.state.trace_id,
                    },
                )
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Trace-ID"] = request.state.trace_id
    return response


app.include_router(auth_router)
app.include_router(company_router)
app.include_router(platform_router)
app.include_router(project_access_router)
app.include_router(support_router)
app.include_router(platform_support_router)


def envelope(request: Request, data: object) -> Envelope:
    return Envelope(data=data, request_id=request.state.request_id, trace_id=request.state.trace_id)


async def project_or_404(session: AsyncSession, project_id: str, user: Principal) -> Project:
    return await accessible_project_or_404(session, project_id, user)


async def run_or_404(session: AsyncSession, run_id: str, user: Principal) -> AgentRun:
    run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id))
    if not run:
        raise HTTPException(404, "Run not found")
    project = await accessible_project_or_404(session, run.project_id, user, include_deleted=True)
    await require_project_permission(session, project, user, "run.view")
    return run


async def idempotent(session: AsyncSession, user: Principal, scope: str, key: str):
    return await session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.tenant_id == user.tenant_id, IdempotencyRecord.scope == scope, IdempotencyRecord.key == key))


async def project_summary(session: AsyncSession, project: Project, user: Principal) -> dict:
    access = await project_access(session, project, user)
    document = await session.scalar(select(ProjectDocument).where(ProjectDocument.project_id == project.id, ProjectDocument.tenant_id == project.tenant_id))
    latest_run = None
    if "run.view" in access["permissions"]:
        latest_run = await session.scalar(select(AgentRun).where(AgentRun.project_id == project.id, AgentRun.tenant_id == project.tenant_id).order_by(AgentRun.created_at.desc()).limit(1))
    latest_approval = None
    if latest_run and "approval.view" in access["permissions"]:
        latest_approval = await session.scalar(select(Approval).where(Approval.run_id == latest_run.id, Approval.tenant_id == project.tenant_id).order_by(Approval.created_at.desc()).limit(1))
    status = project.lifecycle_status
    permissions = sorted(access["permissions"])
    active_run = bool(latest_run and latest_run.status in {"pending", "running", "waiting_approval"})
    start_permission = "run.retry" if status == "blocked" else "run.start"
    return {
        "id": project.id, "name": project.name, "customer_name": project.customer_name,
        "status": status, "lifecycle_status": status, "version": project.version,
        "execution_status": latest_run.status if latest_run else None,
        "created_at": project.created_at.isoformat(), "document": document.content if document else None,
        "latest_run": ({"id": latest_run.id, "run_number": latest_run.run_number,
                        "retry_of_run_id": latest_run.retry_of_run_id, "status": latest_run.status,
                        "version": latest_run.version, "current_node": latest_run.current_node,
                        "updated_at": latest_run.updated_at.isoformat()} if latest_run else None),
        "latest_approval": ({"id": latest_approval.id, "kind": latest_approval.kind,
                             "status": latest_approval.status, "version": latest_approval.version,
                             "comment": latest_approval.comment,
                             "decided_by": latest_approval.decided_by} if latest_approval else None),
        "can_start": status in {"draft", "ready", "blocked"} and not active_run and start_permission in access["permissions"],
        "my_project_role": access["role"], "access_source": access["source"],
        "permissions": permissions,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/metrics", include_in_schema=False, response_class=PlainTextResponse)
async def metrics():
    return "# HELP saas_agent_up Whether the API process is serving requests.\n# TYPE saas_agent_up gauge\nsaas_agent_up 1\n"


@app.post("/api/projects")
async def create_project(payload: ProjectCreate, request: Request, key: str = Depends(idempotency_key), user: Principal = Depends(require_company_admin), session: AsyncSession = Depends(get_session)):
    existing = await idempotent(session, user, "create_project", key)
    if existing:
        return envelope(request, existing.response)
    company_name = payload.customer_name
    if payload.company_id:
        company = await session.get(Tenant, str(payload.company_id))
        if not company or company.status != "active" or company.deleted_at is not None:
            raise HTTPException(404, "所选公司不存在或已停用")
        company_name = company.name
    assisting_company_id = str(payload.assisting_company_id) if payload.assisting_company_id else None
    if assisting_company_id:
        if assisting_company_id == user.tenant_id:
            raise HTTPException(409, "项目归属公司不能作为协助公司")
        assisting_company = await session.get(Tenant, assisting_company_id)
        if not assisting_company or assisting_company.status != "active" or assisting_company.deleted_at is not None:
            raise HTTPException(404, "所选协助公司不存在或已停用")
    document = payload.model_dump(mode="json")
    document["customer_name"] = company_name
    project = Project(tenant_id=user.tenant_id, name=payload.name, customer_name=company_name, requirements_text=payload.requirements_text, created_by=user.user_id, owner_user_id=user.user_id)
    session.add(project)
    await session.flush()
    session.add(ProjectDocument(tenant_id=user.tenant_id, project_id=project.id, content=document))
    session.add(ProjectMembership(
        project_id=project.id,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        project_role="project_manager",
        primary_role_code="project_manager",
        granted_by=user.user_id,
    ))
    if assisting_company_id:
        collaboration = ProjectCollaboration(
            project_id=project.id,
            owner_tenant_id=user.tenant_id,
            tenant_id=assisting_company_id,
            invited_by=user.user_id,
        )
        session.add(collaboration)
        await session.flush()
        session.add(audit_event(request, user, "project.collaboration_invited", collaboration.id, {
            "project_id": project.id, "tenant_id": assisting_company_id,
        }))
    data = await project_summary(session, project, user)
    session.add(IdempotencyRecord(tenant_id=user.tenant_id, scope="create_project", key=key, response=data))
    session.add(audit_event(request, user, "project.created", project.id, {"name": project.name}))
    await session.commit()
    return envelope(request, data)


@app.get("/api/projects")
async def list_projects(request: Request, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    projects = (await session.scalars(select(Project).where(Project.deleted_at.is_(None), accessible_project_filter(user)).order_by(Project.created_at.desc()))).all()
    return envelope(request, [await project_summary(session, project, user) for project in projects])


@app.get("/api/projects/{project_id}")
async def get_project(project_id: UUID, request: Request, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    return envelope(request, await project_summary(session, await project_or_404(session, str(project_id), user), user))


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: UUID, request: Request, key: str = Depends(idempotency_key), user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    existing = await idempotent(session, user, "delete_project", key)
    if existing:
        return envelope(request, existing.response)
    project = await project_or_404(session, str(project_id), user)
    await require_project_permission(session, project, user, "project.delete")
    project.deleted_at, project.deleted_by = datetime.now(UTC), user.user_id
    data = {"id": project.id, "deleted": True, "recoverable_days": 30}
    session.add(IdempotencyRecord(tenant_id=user.tenant_id, scope="delete_project", key=key, response=data))
    session.add(audit_event(request, user, "project.deleted", project.id, {"name": project.name, "soft_delete": True}))
    await session.commit()
    return envelope(request, data)


@app.post("/api/projects/{project_id}/runs")
async def start_run(project_id: UUID, request: Request, key: str = Depends(idempotency_key), user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    project = await project_or_404(session, str(project_id), user)
    permission = "run.retry" if project.lifecycle_status == "blocked" else "run.start"
    await require_project_permission(session, project, user, permission)
    existing = await idempotent(session, user, "start_run", key)
    if existing:
        return envelope(request, existing.response)
    await session.execute(select(Project.id).where(Project.id == project.id).with_for_update())
    latest_run = await session.scalar(select(AgentRun).where(AgentRun.project_id == project.id, AgentRun.tenant_id == project.tenant_id).order_by(AgentRun.run_number.desc()).limit(1))
    if project.lifecycle_status in {"completed", "cancelled", "archived"}:
        raise HTTPException(409, "已完成的项目不能再次启动 Agent")
    if latest_run and latest_run.status in {"pending", "running", "waiting_approval"}:
        raise HTTPException(409, "该项目已有进行中的 Agent，请进入执行详情查看")
    transition_project(project, ProjectLifecycle.IN_PROGRESS)
    next_run_number = (await session.scalar(
        select(func.max(AgentRun.run_number)).where(AgentRun.project_id == project.id)
    ) or 0) + 1
    run = AgentRun(
        tenant_id=project.tenant_id,
        project_id=project.id,
        started_by=user.user_id,
        run_number=next_run_number,
        retry_of_run_id=(latest_run.id if latest_run and latest_run.status in {"failed", "cancelled"} else None),
        trace_id=request.state.trace_id,
    )
    session.add(run)
    await session.flush()
    transition_run(run, RunLifecycle.RUNNING)
    run.state = ImplementationGraphState(project_id=UUID(project.id), run_id=UUID(run.id)).model_dump(mode="json")
    await session.flush()
    await advance(session, run)
    data = {"id": run.id, "run_number": run.run_number, "retry_of_run_id": run.retry_of_run_id,
            "status": run.status, "version": run.version, "current_node": run.current_node}
    session.add(IdempotencyRecord(tenant_id=user.tenant_id, scope="start_run", key=key, response=data))
    await session.commit()
    return envelope(request, data)


@app.post("/api/runs/{run_id}/cancel")
async def cancel_run(
    run_id: UUID,
    request: Request,
    key: str = Depends(idempotency_key),
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    existing = await idempotent(session, user, "cancel_run", key)
    if existing:
        return envelope(request, existing.response)
    run = await run_or_404(session, str(run_id), user)
    project = await project_or_404(session, run.project_id, user)
    await require_project_permission(session, project, user, "run.cancel")
    run = await session.scalar(
        select(AgentRun).where(AgentRun.id == str(run_id)).with_for_update()
    )
    if not run or run.status not in {"pending", "running", "waiting_approval"}:
        raise HTTPException(409, "只有进行中的 Run 可以取消")

    pending_approval = await session.scalar(
        select(Approval).where(
            Approval.run_id == run.id,
            Approval.status == "pending",
        ).with_for_update()
    )
    if pending_approval:
        pending_approval.status = "cancelled"
        pending_approval.comment = "Run 已由项目成员取消"
        pending_approval.decided_by = user.subject
        pending_approval.decided_at = datetime.now(UTC)
        pending_approval.version += 1

    state = ImplementationGraphState.model_validate(run.state)
    state.status = RunStatus.CANCELLED
    state.pending_approval_id = None
    state.updated_at = datetime.now(UTC)
    transition_run(run, RunLifecycle.CANCELLED)
    if project.lifecycle_status == ProjectLifecycle.IN_PROGRESS:
        transition_project(project, ProjectLifecycle.BLOCKED)
    run.state = state.model_dump(mode="json")
    run.updated_at = datetime.now(UTC)
    data = {"id": run.id, "status": run.status, "version": run.version,
            "project_status": project.lifecycle_status}
    session.add(IdempotencyRecord(
        tenant_id=user.tenant_id, scope="cancel_run", key=key, response=data
    ))
    session.add(audit_event(request, user, "run.cancelled", run.id, {
        "project_id": project.id, "run_number": run.run_number,
    }))
    await session.commit()
    return envelope(request, data)


@app.get("/api/runs/{run_id}")
async def get_run(run_id: UUID, request: Request, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    run = await run_or_404(session, str(run_id), user)
    return envelope(request, {"id": run.id, "project_id": run.project_id,
                              "run_number": run.run_number, "retry_of_run_id": run.retry_of_run_id,
                              "status": run.status, "version": run.version,
                              "current_node": run.current_node, "state": run.state,
                              "trace_id": run.trace_id})


@app.get("/api/runs/{run_id}/steps")
async def get_run_steps(run_id: UUID, request: Request, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    run = await run_or_404(session, str(run_id), user)
    rows = (await session.scalars(select(AgentStep).where(AgentStep.run_id == str(run_id), AgentStep.tenant_id == run.tenant_id).order_by(AgentStep.sequence.asc()))).all()
    return envelope(request, [{"id": x.id, "sequence": x.sequence, "node": x.node,
                               "status": x.status, "detail": x.detail,
                               "created_at": x.created_at.isoformat()} for x in rows])


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: UUID, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    await run_or_404(session, str(run_id), user)
    async def stream():
        last = ""
        for _ in range(120):
            run = await session.scalar(select(AgentRun).where(AgentRun.id == str(run_id)))
            if not run:
                yield 'event: error\ndata: {"message":"not found"}\n\n'
                return
            current = json.dumps({"status": run.status, "node": run.current_node, "trace_id": run.trace_id}, ensure_ascii=False)
            if current != last:
                yield f"event: run\ndata: {current}\n\n"
                last = current
            if run.status in {"succeeded", "failed", "cancelled"}:
                return
            await asyncio.sleep(1)
            await session.refresh(run)
    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/approvals")
async def list_approvals(request: Request, run_id: UUID | None = None, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    query = (
        select(Approval)
        .join(AgentRun, AgentRun.id == Approval.run_id)
        .join(Project, Project.id == AgentRun.project_id)
        .where(accessible_project_filter(user))
        .order_by(Approval.created_at.desc())
    )
    if run_id:
        query = query.where(Approval.run_id == str(run_id))
    rows = (await session.scalars(query)).all()
    visible = []
    for item in rows:
        run = await session.get(AgentRun, item.run_id)
        project = await session.get(Project, run.project_id) if run else None
        if not project:
            continue
        try:
            await require_project_permission(session, project, user, "approval.view")
        except HTTPException:
            continue
        visible.append({"id": item.id, "run_id": item.run_id, "kind": item.kind,
                        "status": item.status, "version": item.version, "payload": item.payload,
                        "comment": item.comment, "decided_by": item.decided_by,
                        "decided_at": item.decided_at.isoformat() if item.decided_at else None,
                        "created_at": item.created_at.isoformat()})
    return envelope(request, visible)


@app.post("/api/approvals/{approval_id}/decision")
async def decide(approval_id: UUID, payload: ApprovalDecision, request: Request, key: str = Depends(idempotency_key), user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    existing = await idempotent(session, user, "approval", key)
    if existing:
        return envelope(request, existing.response)
    approval = await session.scalar(
        select(Approval).where(Approval.id == str(approval_id)).with_for_update()
    )
    if not approval or approval.status != "pending":
        raise HTTPException(409, "Approval is not pending")
    if approval.version != payload.expected_version:
        raise HTTPException(409, "Approval version conflict; refresh before deciding")
    run = await session.get(AgentRun, approval.run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    project = await project_or_404(session, run.project_id, user)
    await require_project_permission(session, project, user, "approval.decide")
    if run.started_by == user.user_id or approval.requested_by in {user.subject, user.user_id}:
        raise HTTPException(403, "Requester cannot approve their own high-risk operation")
    approval.status, approval.comment, approval.decided_by = payload.decision, payload.comment, user.subject
    approval.decided_at, approval.version = datetime.now(UTC), approval.version + 1
    run = await resume_after_approval(session, approval, payload.decision)
    data = {"approval_id": approval.id, "run_id": run.id, "run_status": run.status, "current_node": run.current_node}
    session.add(IdempotencyRecord(tenant_id=user.tenant_id, scope="approval", key=key, response=data))
    session.add(audit_event(request, user, f"approval.{payload.decision}", approval.id, {"kind": approval.kind, "comment": payload.comment}))
    await session.commit()
    return envelope(request, data)


@app.post("/api/imports/validate")
async def validate_import(payload: ImportValidateRequest, request: Request, key: str = Depends(idempotency_key), user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    existing = await idempotent(session, user, "validate_import", key)
    if existing:
        return envelope(request, existing.response)
    project = await project_or_404(session, str(payload.project_id), user)
    await require_project_permission(session, project, user, "import.validate")
    result = validate_member_csv(payload.csv_text)
    job = ImportJob(tenant_id=project.tenant_id, project_id=str(payload.project_id), source_hash=hashlib.sha256(payload.csv_text.encode()).hexdigest(), validation=result.model_dump(mode="json"), csv_text=payload.csv_text)
    session.add(job)
    await session.flush()
    data = {"job_id": job.id, **result.model_dump(mode="json")}
    session.add(IdempotencyRecord(tenant_id=user.tenant_id, scope="validate_import", key=key, response=data))
    await session.commit()
    return envelope(request, data)


@app.post("/api/imports/{job_id}/execute")
async def execute_import(job_id: UUID, request: Request, key: str = Depends(idempotency_key), approval_id: UUID | None = None, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    existing = await idempotent(session, user, "execute_import", key)
    if existing:
        return envelope(request, existing.response)
    job = await session.scalar(select(ImportJob).where(ImportJob.id == str(job_id)))
    if not job:
        raise HTTPException(404, "Import job not found")
    project = await project_or_404(session, job.project_id, user)
    await require_project_permission(session, project, user, "import.execute")
    if not job.validation.get("valid"):
        raise HTTPException(409, "Invalid CSV cannot be imported")
    approval = await session.scalar(select(Approval).where(Approval.id == str(approval_id), Approval.tenant_id == project.tenant_id)) if approval_id else None
    if not approval or approval.kind != "import" or approval.status != "approved":
        raise HTTPException(403, "An approved import approval is required")
    job.status, job.result = "completed", {"successful": job.validation["row_count"], "failed": 0, "verified": True}
    data = {"job_id": job.id, **job.result}
    session.add(IdempotencyRecord(tenant_id=user.tenant_id, scope="execute_import", key=key, response=data))
    session.add(audit_event(request, user, "import.executed", job.id, data))
    await session.commit()
    return envelope(request, data)


async def latest_run_for_project(session: AsyncSession, project_id: str, user: Principal):
    project = await project_or_404(session, project_id, user)
    run = await session.scalar(select(AgentRun).where(AgentRun.project_id == project_id, AgentRun.tenant_id == project.tenant_id).order_by(AgentRun.created_at.desc()))
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@app.get("/api/projects/{project_id}/plan")
async def project_plan(project_id: UUID, request: Request, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    return envelope(request, (await latest_run_for_project(session, str(project_id), user)).state.get("plan"))


@app.get("/api/projects/{project_id}/go-live-report")
async def report(project_id: UUID, request: Request, user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    return envelope(request, (await latest_run_for_project(session, str(project_id), user)).state.get("acceptance_report"))

