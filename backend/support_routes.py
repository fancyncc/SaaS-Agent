from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import audit_event
from backend.db import get_session
from backend.models import Project, SupportAccessGrant, SupportAccessGrantPermission
from backend.permissions import SUPPORT_READ_PERMISSIONS
from backend.schemas import SupportAccessCreateRequest, SupportAccessDecisionRequest
from backend.security import (
    Principal,
    current_principal,
    idempotency_key,
    require_company_admin,
    require_platform_roles,
)

router = APIRouter(prefix="/api/company/support-access-grants", tags=["company support access"])
platform_router = APIRouter(prefix="/api/platform/support-access-grants", tags=["platform support access"])


async def _support_project(session: AsyncSession, project_id: str, tenant_id: str):
    if session.bind and session.bind.dialect.name == "postgresql":
        return (await session.execute(text(
            "SELECT id, tenant_id, name, customer_name FROM support_project_directory "
            "WHERE id=:project_id AND tenant_id=:tenant_id"
        ), {"project_id": project_id, "tenant_id": tenant_id})).mappings().first()
    project = await session.get(Project, project_id)
    if not project or project.deleted_at is not None or project.tenant_id != tenant_id:
        return None
    return {"id": project.id, "tenant_id": project.tenant_id,
            "name": project.name, "customer_name": project.customer_name}


async def _payload(session: AsyncSession, item: SupportAccessGrant) -> dict:
    permissions = (await session.scalars(select(SupportAccessGrantPermission.permission_code).where(
        SupportAccessGrantPermission.grant_id == item.id,
    ).order_by(SupportAccessGrantPermission.permission_code))).all()
    now = datetime.now(UTC)
    expiry = item.expires_at if item.expires_at.tzinfo else item.expires_at.replace(tzinfo=UTC)
    status = "expired" if item.status == "approved" and not item.revoked_at and expiry <= now else item.status
    return {
        "id": item.id,
        "requester_user_id": item.requester_user_id,
        "target_tenant_id": item.target_tenant_id,
        "target_project_id": item.target_project_id,
        "reason": item.reason,
        "permission_codes": list(permissions),
        "status": status,
        "requested_at": item.requested_at.isoformat(),
        "expires_at": item.expires_at.isoformat(),
        "decided_by": item.decided_by,
        "decided_at": item.decided_at.isoformat() if item.decided_at else None,
        "revoked_by": item.revoked_by,
        "revoked_at": item.revoked_at.isoformat() if item.revoked_at else None,
    }


@router.get("")
async def list_support_access(
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    condition = SupportAccessGrant.target_tenant_id == user.tenant_id
    rows = (await session.scalars(select(SupportAccessGrant).where(condition).order_by(
        SupportAccessGrant.requested_at.desc()
    ))).all()
    return {"data": [await _payload(session, item) for item in rows]}


@platform_router.get("/targets/{tenant_id}")
async def support_targets(
    tenant_id: UUID,
    user: Principal = Depends(require_platform_roles("platform_super_admin", "platform_support")),
    session: AsyncSession = Depends(get_session),
):
    if session.bind and session.bind.dialect.name == "postgresql":
        rows = (await session.execute(text(
            "SELECT id,name,customer_name FROM support_project_directory "
            "WHERE tenant_id=:tenant_id ORDER BY name"
        ), {"tenant_id": str(tenant_id)})).mappings().all()
        return {"data": [dict(item) for item in rows]}
    rows = (await session.scalars(select(Project).where(
        Project.tenant_id == str(tenant_id), Project.deleted_at.is_(None),
    ).order_by(Project.name))).all()
    return {"data": [{"id": item.id, "name": item.name, "customer_name": item.customer_name} for item in rows]}


@platform_router.post("")
async def create_support_access(
    payload: SupportAccessCreateRequest,
    request: Request,
    _: str = Depends(idempotency_key),
    user: Principal = Depends(require_platform_roles("platform_super_admin", "platform_support")),
    session: AsyncSession = Depends(get_session),
):
    project = await _support_project(session, str(payload.target_project_id), str(payload.target_tenant_id))
    if not project:
        raise HTTPException(404, "目标项目不存在")
    requested_permissions = set(payload.permission_codes)
    if not requested_permissions or not requested_permissions.issubset(SUPPORT_READ_PERMISSIONS):
        raise HTTPException(422, "支持访问只能申请允许的只读权限")
    now = datetime.now(UTC)
    expires_at = payload.expires_at or (now + timedelta(hours=2))
    expires_at = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)
    if expires_at <= now or expires_at > now + timedelta(hours=8):
        raise HTTPException(422, "支持访问有效期必须大于当前时间且不超过 8 小时")
    item = SupportAccessGrant(
        requester_user_id=user.user_id,
        target_tenant_id=project["tenant_id"],
        target_project_id=project["id"],
        reason=payload.reason.strip(),
        requested_at=now,
        expires_at=expires_at,
    )
    session.add(item)
    await session.flush()
    for code in sorted(requested_permissions):
        session.add(SupportAccessGrantPermission(grant_id=item.id, permission_code=code))
    session.add(audit_event(request, user, "support.access_requested", item.id, {
        "target_tenant_id": project["tenant_id"],
        "target_project_id": project["id"],
        "permission_codes": sorted(requested_permissions),
        "expires_at": expires_at.isoformat(),
    }))
    await session.commit()
    return {"data": await _payload(session, item)}


async def _decide(
    grant_id: str,
    decision: str,
    payload: SupportAccessDecisionRequest,
    request: Request,
    user: Principal,
    session: AsyncSession,
):
    if user.company_role_code != "company_admin":
        raise HTTPException(403, "只有目标公司管理员可以处理支持访问申请")
    item = await session.get(SupportAccessGrant, grant_id)
    if not item or item.target_tenant_id != user.tenant_id:
        raise HTTPException(404, "支持访问申请不存在")
    if item.requester_user_id == user.user_id:
        raise HTTPException(403, "申请人不能审批自己的支持访问")
    if item.status != "pending":
        raise HTTPException(409, "支持访问申请已经处理")
    now = datetime.now(UTC)
    expiry = item.expires_at if item.expires_at.tzinfo else item.expires_at.replace(tzinfo=UTC)
    if expiry <= now:
        raise HTTPException(410, "支持访问申请已过期")
    item.status = decision
    item.decided_by = user.user_id
    item.decided_at = now
    session.add(audit_event(request, user, f"support.access_{decision}", item.id, {
        "target_project_id": item.target_project_id, "comment": payload.comment,
    }))
    await session.commit()
    return {"data": await _payload(session, item)}


@router.post("/{grant_id}/approve")
async def approve_support_access(
    grant_id: UUID,
    payload: SupportAccessDecisionRequest,
    request: Request,
    _: str = Depends(idempotency_key),
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    return await _decide(str(grant_id), "approved", payload, request, user, session)


@router.post("/{grant_id}/reject")
async def reject_support_access(
    grant_id: UUID,
    payload: SupportAccessDecisionRequest,
    request: Request,
    _: str = Depends(idempotency_key),
    user: Principal = Depends(require_company_admin),
    session: AsyncSession = Depends(get_session),
):
    return await _decide(str(grant_id), "rejected", payload, request, user, session)


async def _revoke_support_access(
    grant_id: UUID,
    request: Request,
    _: str = Depends(idempotency_key),
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    item = await session.get(SupportAccessGrant, str(grant_id))
    if not item:
        raise HTTPException(404, "支持访问申请不存在")
    allowed = (user.session_context == "platform" and item.requester_user_id == user.user_id) or (
        user.session_context == "customer" and user.company_role_code == "company_admin"
        and item.target_tenant_id == user.tenant_id)
    if not allowed:
        raise HTTPException(403, "无权撤销该支持访问")
    if not item.revoked_at:
        item.status = "revoked"
        item.revoked_by = user.user_id
        item.revoked_at = datetime.now(UTC)
        session.add(audit_event(request, user, "support.access_revoked", item.id, {
            "target_project_id": item.target_project_id,
        }))
        await session.commit()
    return {"data": await _payload(session, item)}


@router.post("/{grant_id}/revoke")
async def company_revoke_support_access(
    grant_id: UUID, request: Request, _: str = Depends(idempotency_key),
    user: Principal = Depends(require_company_admin), session: AsyncSession = Depends(get_session),
):
    return await _revoke_support_access(grant_id, request, _, user, session)


@platform_router.get("")
async def list_platform_support_access(
    user: Principal = Depends(require_platform_roles("platform_super_admin", "platform_support")),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.scalars(select(SupportAccessGrant).where(
        SupportAccessGrant.requester_user_id == user.user_id
    ).order_by(SupportAccessGrant.requested_at.desc()))).all()
    return {"data": [await _payload(session, item) for item in rows]}


@platform_router.post("/{grant_id}/revoke")
async def platform_revoke_support_access(
    grant_id: UUID, request: Request, _: str = Depends(idempotency_key),
    user: Principal = Depends(require_platform_roles("platform_super_admin", "platform_support")),
    session: AsyncSession = Depends(get_session),
):
    return await _revoke_support_access(grant_id, request, _, user, session)
