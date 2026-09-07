from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import audit_event
from backend.config import get_settings
from backend.db import get_session
from backend.evaluation import run_fixed_evaluation
from backend.mailer import send_account_link
from backend.models import (
    AgentRun,
    AuditEvent,
    AuthSession,
    EvaluationRun,
    PlatformInvitation,
    PlatformRoleBinding,
    Project,
    ProjectCapabilityGrant,
    ProjectMembership,
    Tenant,
    TenantMembership,
    User,
    UserInvitation,
)
from backend.schemas import (
    CompanyTransferRequest,
    PlatformInvitationCreateRequest,
    PlatformUserUpdateRequest,
    TenantCreateRequest,
    TenantUpdateRequest,
)
from backend.security import Principal, idempotency_key, require_platform_roles, token_hash

router = APIRouter(prefix="/api/platform", tags=["platform administration"])
SUPER = "platform_super_admin"
GOVERNANCE = (SUPER, "platform_operator")


def _tenant_payload(item: Tenant) -> dict:
    return {"id": item.id, "name": item.name, "slug": item.slug, "status": item.status,
            "deleted_at": item.deleted_at.isoformat() if item.deleted_at else None}


@router.get("/dashboard")
async def dashboard(
    _: Principal = Depends(require_platform_roles(SUPER, "platform_operator", "platform_support", "platform_auditor")),
    session: AsyncSession = Depends(get_session),
):
    return {"data": {
        "tenant_count": await session.scalar(select(func.count()).select_from(Tenant).where(Tenant.kind == "company", Tenant.deleted_at.is_(None))) or 0,
        "customer_user_count": await session.scalar(select(func.count()).select_from(User).where(User.account_type == "customer", User.deleted_at.is_(None))) or 0,
        "platform_user_count": await session.scalar(select(func.count()).select_from(User).where(User.account_type == "platform", User.deleted_at.is_(None))) or 0,
        "failed_run_count": await session.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.status == "failed")) or 0,
    }}


@router.get("/tenants")
async def list_tenants(
    _: Principal = Depends(require_platform_roles(SUPER, "platform_operator", "platform_support", "platform_auditor")),
    session: AsyncSession = Depends(get_session),
):
    return {"data": [_tenant_payload(x) for x in (await session.scalars(
        select(Tenant).where(Tenant.kind == "company").order_by(Tenant.created_at.desc())
    )).all()]}


@router.post("/tenants")
async def create_tenant(
    payload: TenantCreateRequest, request: Request,
    user: Principal = Depends(require_platform_roles(*GOVERNANCE)),
    session: AsyncSession = Depends(get_session),
):
    if await session.scalar(select(Tenant).where(Tenant.slug == payload.slug)):
        raise HTTPException(409, "租户标识已存在")
    existing = await session.scalar(select(User).where(User.email == payload.admin_email.strip().lower()))
    if existing:
        raise HTTPException(409, "该邮箱已有账号；请先在用户治理中确认归属")
    tenant = Tenant(name=payload.name.strip(), slug=payload.slug)
    session.add(tenant)
    await session.flush()
    raw = secrets.token_urlsafe(48)
    invitation = UserInvitation(
        tenant_id=tenant.id, email=payload.admin_email.strip().lower(),
        display_name=payload.admin_name.strip(), role="tenant_admin",
        token_hash=token_hash(raw), invited_by=user.user_id,
        expires_at=datetime.now(UTC) + timedelta(hours=48),
    )
    session.add(invitation)
    session.add(audit_event(request, user, "tenant.created", tenant.id, {"name": tenant.name}))
    url = f"{get_settings().frontend_base_url}/accept-invitation?token={quote(raw)}"
    await send_account_link(invitation.email, "invitation", url, session=session)
    await session.commit()
    data = {**_tenant_payload(tenant), "invitation_id": invitation.id}
    if get_settings().mail_debug:
        data["invitation_url"] = url
    return {"data": data}


@router.patch("/tenants/{tenant_id}")
async def update_tenant(
    tenant_id: UUID, payload: TenantUpdateRequest, request: Request,
    user: Principal = Depends(require_platform_roles(*GOVERNANCE)),
    session: AsyncSession = Depends(get_session),
):
    tenant = await session.get(Tenant, str(tenant_id))
    if not tenant:
        raise HTTPException(404, "公司不存在")
    before = {"name": tenant.name, "status": tenant.status}
    if payload.name is not None:
        tenant.name = payload.name.strip()
    if payload.status is not None:
        tenant.status = payload.status
    tenant.updated_at = datetime.now(UTC)
    session.add(audit_event(request, user, "tenant.updated", tenant.id, {
        "before": before, "after": {"name": tenant.name, "status": tenant.status},
    }))
    await session.commit()
    return {"data": _tenant_payload(tenant)}


@router.get("/users")
async def list_users(
    q: str | None = None, tenant_id: UUID | None = None,
    _: Principal = Depends(require_platform_roles(SUPER, "platform_operator", "platform_support", "platform_auditor")),
    session: AsyncSession = Depends(get_session),
):
    query = (select(User, TenantMembership, Tenant)
             .outerjoin(TenantMembership, (TenantMembership.user_id == User.id) & (TenantMembership.status == "active") & (TenantMembership.workspace_kind == "company"))
             .outerjoin(Tenant, Tenant.id == TenantMembership.tenant_id)
             .where(User.deleted_at.is_(None)))
    if q:
        pattern = f"%{q.strip().lower()}%"
        query = query.where(func.lower(User.email).like(pattern) | func.lower(User.display_name).like(pattern))
    if tenant_id:
        query = query.where(TenantMembership.tenant_id == str(tenant_id))
    rows = (await session.execute(query.order_by(User.created_at.desc()))).all()
    return {"data": [{
        "id": user.id, "email": user.email, "display_name": user.display_name,
        "account_type": user.account_type, "status": user.status,
        "tenant_id": membership.tenant_id if membership else None,
        "tenant_name": tenant.name if tenant else None,
        "company_role_code": membership.company_role_code if membership else None,
        "membership_id": membership.id if membership else None,
    } for user, membership, tenant in rows]}


@router.patch("/users/{user_id}")
async def update_user(
    user_id: UUID, payload: PlatformUserUpdateRequest, request: Request,
    principal: Principal = Depends(require_platform_roles(*GOVERNANCE)),
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, str(user_id))
    if not user or user.deleted_at is not None:
        raise HTTPException(404, "用户不存在")
    if user.id == principal.user_id and payload.status == "disabled":
        raise HTTPException(409, "不能停用自己的平台账号")
    before = {"display_name": user.display_name, "status": user.status}
    if payload.display_name is not None:
        user.display_name = payload.display_name.strip()
    if payload.status is not None:
        user.status = payload.status
        if payload.status == "disabled":
            await session.execute(update(AuthSession).where(
                AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)
            ).values(revoked_at=datetime.now(UTC)))
    session.add(audit_event(request, principal, "platform.user_updated", user.id, {
        "before": before, "after": {"display_name": user.display_name, "status": user.status},
    }))
    await session.commit()
    return {"data": {"id": user.id, "display_name": user.display_name, "status": user.status}}


@router.post("/users/{user_id}/transfer-company")
async def transfer_company(
    user_id: UUID, payload: CompanyTransferRequest, request: Request,
    principal: Principal = Depends(require_platform_roles(*GOVERNANCE)),
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, str(user_id))
    target = await session.get(Tenant, str(payload.target_tenant_id))
    if not user or user.account_type != "customer" or user.deleted_at is not None:
        raise HTTPException(404, "客户用户不存在")
    if not target or target.kind != "company" or target.status != "active" or target.deleted_at is not None:
        raise HTTPException(404, "目标公司不可用")
    current = await session.scalar(select(TenantMembership).where(
        TenantMembership.user_id == user.id, TenantMembership.status == "active", TenantMembership.workspace_kind == "company"
    ))
    if current and current.tenant_id == target.id:
        raise HTTPException(409, "用户已经属于目标公司")
    if current and current.company_role_code == "company_admin":
        admin_count = await session.scalar(select(func.count()).select_from(TenantMembership).where(
            TenantMembership.tenant_id == current.tenant_id,
            TenantMembership.company_role_code == "company_admin",
            TenantMembership.status == "active",
        ))
        if (admin_count or 0) <= 1:
            raise HTTPException(409, "不能转移该公司的最后一名有效管理员")
    now = datetime.now(UTC)
    old_tenant_id = current.tenant_id if current else None
    if current:
        current.status = "disabled"
        project_memberships = list((await session.scalars(select(ProjectMembership).where(
            ProjectMembership.user_id == user.id,
            ProjectMembership.tenant_id == current.tenant_id,
            ProjectMembership.status == "active",
        ))).all())
        for membership in project_memberships:
            membership.status = "disabled"
            await session.execute(update(ProjectCapabilityGrant).where(
                ProjectCapabilityGrant.membership_id == membership.id,
                ProjectCapabilityGrant.revoked_at.is_(None),
            ).values(revoked_at=now, revoked_by=principal.user_id))
    target_membership = await session.scalar(select(TenantMembership).where(
        TenantMembership.user_id == user.id, TenantMembership.tenant_id == target.id
    ))
    role = payload.company_role.value
    company_role = "company_admin" if role == "tenant_admin" else "company_member"
    if target_membership:
        target_membership.role = role
        target_membership.company_role_code = company_role
        target_membership.status = "active"
    else:
        target_membership = TenantMembership(tenant_id=target.id, user_id=user.id,
                                             role=role, company_role_code=company_role)
        session.add(target_membership)
    await session.execute(update(AuthSession).where(
        AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)
    ).values(revoked_at=now))
    await session.execute(update(UserInvitation).where(
        UserInvitation.email == user.email, UserInvitation.status == "pending"
    ).values(status="revoked"))
    session.add(audit_event(request, principal, "membership.company_transferred", user.id, {
        "from_tenant_id": old_tenant_id, "to_tenant_id": target.id,
        "company_role": company_role, "reason": payload.reason.strip(),
    }))
    await session.commit()
    return {"data": {"user_id": user.id, "tenant_id": target.id,
                     "tenant_name": target.name, "company_role_code": company_role}}


@router.post("/users/{user_id}/revoke-sessions")
async def revoke_user_sessions(
    user_id: UUID, request: Request,
    principal: Principal = Depends(require_platform_roles(*GOVERNANCE)),
    session: AsyncSession = Depends(get_session),
):
    if str(user_id) == principal.user_id:
        raise HTTPException(409, "不能在当前会话中撤销自己的全部会话")
    revoked_ids = (await session.scalars(update(AuthSession).where(
        AuthSession.user_id == str(user_id), AuthSession.revoked_at.is_(None)
    ).values(revoked_at=datetime.now(UTC)).returning(AuthSession.id))).all()
    session.add(audit_event(request, principal, "platform.sessions_revoked", str(user_id),
                            {"count": len(revoked_ids)}))
    await session.commit()
    return {"data": {"revoked": len(revoked_ids)}}


@router.get("/staff")
async def list_staff(
    _: Principal = Depends(require_platform_roles(SUPER)),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(select(User, PlatformRoleBinding).join(
        PlatformRoleBinding, PlatformRoleBinding.user_id == User.id
    ).where(User.account_type == "platform").order_by(User.display_name))).all()
    return {"data": [{"user_id": user.id, "email": user.email,
                      "display_name": user.display_name, "status": user.status,
                      "role_code": binding.role_code, "binding_status": binding.status}
                     for user, binding in rows]}


@router.post("/staff/invitations")
async def invite_staff(
    payload: PlatformInvitationCreateRequest, request: Request,
    principal: Principal = Depends(require_platform_roles(SUPER)),
    session: AsyncSession = Depends(get_session),
):
    email = payload.email.strip().lower()
    existing = await session.scalar(select(User).where(User.email == email))
    if existing and existing.account_type != "platform":
        raise HTTPException(409, "客户公司账号不能转换为平台账号")
    await session.execute(update(PlatformInvitation).where(
        PlatformInvitation.email == email, PlatformInvitation.status == "pending"
    ).values(status="revoked"))
    raw = secrets.token_urlsafe(48)
    item = PlatformInvitation(email=email, display_name=payload.display_name.strip(),
                              role_code=payload.role_code.value, token_hash=token_hash(raw),
                              invited_by=principal.user_id,
                              expires_at=datetime.now(UTC) + timedelta(hours=24))
    session.add(item)
    await session.flush()
    session.add(audit_event(request, principal, "platform.staff_invited", item.id,
                            {"email": email, "role_code": item.role_code}))
    url = f"{get_settings().frontend_base_url}/accept-platform-invitation?token={quote(raw)}"
    await send_account_link(email, "platform_invitation", url, session=session)
    await session.commit()
    data = {"id": item.id, "expires_at": item.expires_at.isoformat()}
    if get_settings().mail_debug:
        data["invitation_url"] = url
    return {"data": data}


@router.get("/audit-events")
async def global_audit(
    _: Principal = Depends(require_platform_roles(SUPER, "platform_operator", "platform_auditor")),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(500))).all()
    return {"data": [{"id": x.id, "tenant_id": x.tenant_id, "event_type": x.event_type,
                      "actor": x.actor, "resource_id": x.resource_id, "outcome": x.outcome,
                      "payload": x.payload, "created_at": x.created_at.isoformat()} for x in rows]}


@router.get("/system-status")
async def system_status(
    _: Principal = Depends(require_platform_roles(SUPER, "platform_support", "platform_auditor")),
    session: AsyncSession = Depends(get_session),
):
    return {"data": {"database": "ok", "checked_at": datetime.now(UTC).isoformat(),
                     "active_sessions": await session.scalar(select(func.count()).select_from(AuthSession).where(
                         AuthSession.revoked_at.is_(None), AuthSession.expires_at > datetime.now(UTC))) or 0}}


@router.post("/evaluations/run")
async def run_evaluation(
    request: Request,
    key: str = Depends(idempotency_key),
    user: Principal = Depends(require_platform_roles(SUPER)),
    session: AsyncSession = Depends(get_session),
):
    # Serialize requests from one actor before checking the idempotency receipt.
    await session.execute(select(User.id).where(User.id == user.user_id).with_for_update())
    request_hash = hashlib.sha256(f"{user.user_id}:{key}".encode()).hexdigest()
    previous = await session.scalar(select(EvaluationRun).where(EvaluationRun.tenant_id.is_(None), EvaluationRun.result["request_key_hash"].as_string() == request_hash))
    if previous:
        return {"data": {"id": previous.id, "status": previous.status, **previous.result}}
    result = run_fixed_evaluation()
    result["request_key_hash"] = request_hash
    item = EvaluationRun(tenant_id=None, dataset_size=str(result["dataset_size"]), result=result)
    session.add(item)
    await session.flush()
    data = {"id": item.id, "status": item.status, **result}
    session.add(audit_event(request, user, "evaluation.completed", item.id, {"dataset_checksum": result["dataset_checksum"], "mode": result["mode"]}))
    await session.commit()
    return {"data": data}


@router.get("/evaluations")
async def list_evaluations(
    _: Principal = Depends(require_platform_roles(SUPER, "platform_auditor")),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.scalars(select(EvaluationRun).where(EvaluationRun.tenant_id.is_(None)).order_by(EvaluationRun.created_at.desc(), EvaluationRun.id.desc()).limit(100))).all()
    return {"data": [{"id": row.id, "status": row.status, "created_at": row.created_at.isoformat(), "result": row.result} for row in rows]}


@router.get("/evaluations/{evaluation_id}/download")
async def download_evaluation(
    evaluation_id: UUID,
    _: Principal = Depends(require_platform_roles(SUPER, "platform_auditor")),
    session: AsyncSession = Depends(get_session),
):
    item = await session.scalar(select(EvaluationRun).where(EvaluationRun.id == str(evaluation_id), EvaluationRun.tenant_id.is_(None)))
    if item is None:
        raise HTTPException(404, "评测不存在")
    return JSONResponse({"id": item.id, "status": item.status, "created_at": item.created_at.isoformat(), "result": item.result}, headers={"Content-Disposition": f'attachment; filename="evaluation-{item.id}.json"'})


@router.get("/inspect/tenants/{tenant_id}/projects")
async def inspect_projects(
    tenant_id: UUID, _: Principal = Depends(require_platform_roles(SUPER)),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.scalars(select(Project).where(
        Project.tenant_id == str(tenant_id), Project.deleted_at.is_(None)
    ).order_by(Project.created_at.desc()))).all()
    return {"data": [{"id": x.id, "name": x.name, "customer_name": x.customer_name,
                      "lifecycle_status": x.lifecycle_status, "created_at": x.created_at.isoformat()}
                     for x in rows]}


@router.get("/inspect/projects/{project_id}")
async def inspect_project(
    project_id: UUID, _: Principal = Depends(require_platform_roles(SUPER)),
    session: AsyncSession = Depends(get_session),
):
    project = await session.get(Project, str(project_id))
    if not project or project.deleted_at is not None:
        raise HTTPException(404, "项目不存在")
    runs = (await session.scalars(select(AgentRun).where(
        AgentRun.project_id == project.id
    ).order_by(AgentRun.created_at.desc()))).all()
    return {"data": {"id": project.id, "tenant_id": project.tenant_id,
                     "name": project.name, "customer_name": project.customer_name,
                     "requirements_text": project.requirements_text,
                     "lifecycle_status": project.lifecycle_status,
                     "runs": [{"id": x.id, "run_number": x.run_number, "status": x.status,
                               "current_node": x.current_node, "created_at": x.created_at.isoformat()}
                              for x in runs]}}
