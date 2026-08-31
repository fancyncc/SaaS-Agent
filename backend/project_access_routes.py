from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.access import accessible_project_or_404, require_project_permission
from backend.audit import audit_event
from backend.db import get_session
from backend.models import (
    ProjectCapabilityGrant,
    ProjectCollaboration,
    ProjectMembership,
    Tenant,
    TenantMembership,
    User,
)
from backend.permissions import GRANTABLE_CAPABILITIES, PROJECT_ROLES
from backend.schemas import (
    CapabilityGrantCreateRequest,
    ProjectCollaborationCreateRequest,
    ProjectMemberCreateRequest,
    ProjectMemberUpdateRequest,
)
from backend.security import Principal, current_principal, idempotency_key

router = APIRouter(prefix="/api", tags=["project access"])


@router.get("/role-catalog")
async def role_catalog(user: Principal = Depends(current_principal)):
    return {"data": {
        "company_role_code": user.company_role_code,
        "project_roles": [{"code": code, "name": name} for code, name in PROJECT_ROLES.items()],
        "grantable_capabilities": sorted(GRANTABLE_CAPABILITIES),
    }}


async def _company_member(session: AsyncSession, user_id: str) -> TenantMembership:
    membership = await session.scalar(select(TenantMembership).where(
        TenantMembership.user_id == user_id,
        TenantMembership.status == "active",
    ))
    if not membership:
        raise HTTPException(404, "成员不存在或已停用")
    return membership


@router.get("/project-collaborations/pending")
async def pending_collaborations(
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    if user.company_role_code != "company_admin":
        raise HTTPException(403, "只有公司管理员可以查看协作邀请")
    query = (
        select(ProjectCollaboration, Tenant)
        .join(Tenant, Tenant.id == ProjectCollaboration.owner_tenant_id)
        .where(ProjectCollaboration.status == "pending")
        .order_by(ProjectCollaboration.created_at.desc())
    )
    query = query.where(ProjectCollaboration.tenant_id == user.tenant_id)
    rows = (await session.execute(query)).all()
    return {"data": [{
        "id": item.id, "project_id": item.project_id,
        "owner_tenant_id": tenant.id, "owner_tenant_name": tenant.name,
        "tenant_id": item.tenant_id, "status": item.status,
        "created_at": item.created_at.isoformat(),
    } for item, tenant in rows]}


@router.get("/projects/{project_id}/members")
async def list_project_members(
    project_id: UUID,
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    project = await accessible_project_or_404(session, str(project_id), user)
    await require_project_permission(session, project, user, "project.member.view")
    rows = (await session.execute(
        select(ProjectMembership, User, Tenant)
        .join(User, User.id == ProjectMembership.user_id)
        .join(Tenant, Tenant.id == ProjectMembership.tenant_id)
        .where(ProjectMembership.project_id == project.id)
        .order_by(Tenant.name, User.display_name)
    )).all()
    return {"data": [{
        "id": item.id, "user_id": member.id, "display_name": member.display_name,
        "email": member.email, "tenant_id": tenant.id, "tenant_name": tenant.name,
        "primary_role_code": item.primary_role_code,
        "project_role": item.project_role,
        "status": item.status,
    } for item, member, tenant in rows]}


@router.post("/projects/{project_id}/members")
async def add_project_member(
    project_id: UUID,
    payload: ProjectMemberCreateRequest,
    request: Request,
    _: str = Depends(idempotency_key),
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    project = await accessible_project_or_404(session, str(project_id), user)
    await require_project_permission(session, project, user, "project.member.assign")
    target = await _company_member(session, str(payload.user_id))
    collaboration = await session.scalar(select(ProjectCollaboration).where(
        ProjectCollaboration.project_id == project.id,
        ProjectCollaboration.tenant_id == target.tenant_id,
        ProjectCollaboration.status == "active",
    ))
    if target.tenant_id != project.tenant_id and not collaboration:
        raise HTTPException(409, "该成员所属公司尚未接受项目协作")
    if target.tenant_id != user.tenant_id:
        raise HTTPException(403, "只能为本公司成员分配项目权限")
    item = await session.scalar(select(ProjectMembership).where(
        ProjectMembership.project_id == project.id,
        ProjectMembership.user_id == target.user_id,
    ))
    if item:
        role_code = payload.primary_role_code.value
        item.project_role = item.primary_role_code = role_code
        item.status, item.granted_by = "active", user.user_id
        item.tenant_id, item.updated_at = target.tenant_id, datetime.now(UTC)
    else:
        item = ProjectMembership(
            project_id=project.id, tenant_id=target.tenant_id, user_id=target.user_id,
            project_role=payload.primary_role_code.value,
            primary_role_code=payload.primary_role_code.value,
            granted_by=user.user_id,
        )
        session.add(item)
        await session.flush()
    session.add(audit_event(request, user, "project.member_granted", item.id, {
        "project_id": project.id, "user_id": target.user_id,
        "primary_role_code": item.primary_role_code, "project_role": item.project_role,
    }))
    await session.commit()
    return {"data": {"id": item.id, "primary_role_code": item.primary_role_code,
                       "project_role": item.project_role, "status": item.status}}


@router.patch("/projects/{project_id}/members/{membership_id}")
async def update_project_member(
    project_id: UUID,
    membership_id: UUID,
    payload: ProjectMemberUpdateRequest,
    request: Request,
    _: str = Depends(idempotency_key),
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    project = await accessible_project_or_404(session, str(project_id), user)
    await require_project_permission(session, project, user, "project.member.assign")
    item = await session.get(ProjectMembership, str(membership_id))
    if not item or item.project_id != project.id:
        raise HTTPException(404, "项目成员不存在")
    if item.tenant_id != user.tenant_id:
        raise HTTPException(403, "只能调整本公司成员")
    if payload.primary_role_code is not None:
        item.project_role = item.primary_role_code = payload.primary_role_code.value
    if payload.status is not None:
        item.status = payload.status
    item.updated_at = datetime.now(UTC)
    session.add(audit_event(request, user, "project.member_updated", item.id, {
        "project_id": project.id, "primary_role_code": item.primary_role_code,
        "project_role": item.project_role, "status": item.status,
    }))
    await session.commit()
    return {"data": {"id": item.id, "primary_role_code": item.primary_role_code,
                       "project_role": item.project_role, "status": item.status}}


@router.delete("/projects/{project_id}/members/{membership_id}")
async def remove_project_member(
    project_id: UUID,
    membership_id: UUID,
    request: Request,
    _: str = Depends(idempotency_key),
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    project = await accessible_project_or_404(session, str(project_id), user)
    await require_project_permission(session, project, user, "project.member.revoke")
    item = await session.get(ProjectMembership, str(membership_id))
    if not item or item.project_id != project.id:
        raise HTTPException(404, "项目成员不存在")
    if item.tenant_id != user.tenant_id:
        raise HTTPException(403, "只能移除本公司成员")
    item.status, item.updated_at = "disabled", datetime.now(UTC)
    session.add(audit_event(request, user, "project.member_removed", item.id, {"project_id": project.id}))
    await session.commit()
    return {"data": {"removed": True, "id": item.id}}


def _grant_payload(item: ProjectCapabilityGrant) -> dict:
    now = datetime.now(UTC)
    expires_at = item.expires_at if item.expires_at.tzinfo else item.expires_at.replace(tzinfo=UTC)
    status = "revoked" if item.revoked_at else "expired" if expires_at <= now else "active"
    return {
        "id": item.id,
        "membership_id": item.membership_id,
        "permission_code": item.permission_code,
        "reason": item.reason,
        "granted_by": item.granted_by,
        "granted_at": item.granted_at.isoformat(),
        "expires_at": item.expires_at.isoformat(),
        "revoked_at": item.revoked_at.isoformat() if item.revoked_at else None,
        "status": status,
    }


@router.get("/projects/{project_id}/members/{membership_id}/capability-grants")
async def list_capability_grants(
    project_id: UUID,
    membership_id: UUID,
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    project = await accessible_project_or_404(session, str(project_id), user)
    await require_project_permission(session, project, user, "project.member.view")
    membership = await session.get(ProjectMembership, str(membership_id))
    if not membership or membership.project_id != project.id:
        raise HTTPException(404, "项目成员不存在")
    rows = (await session.scalars(select(ProjectCapabilityGrant).where(
        ProjectCapabilityGrant.membership_id == membership.id,
    ).order_by(ProjectCapabilityGrant.granted_at.desc()))).all()
    return {"data": [_grant_payload(item) for item in rows]}


@router.post("/projects/{project_id}/members/{membership_id}/capability-grants")
async def create_capability_grant(
    project_id: UUID,
    membership_id: UUID,
    payload: CapabilityGrantCreateRequest,
    request: Request,
    _: str = Depends(idempotency_key),
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    if user.company_role_code != "company_admin":
        raise HTTPException(403, "只有公司管理员可以授予临时能力")
    project = await accessible_project_or_404(session, str(project_id), user)
    membership = await session.get(ProjectMembership, str(membership_id))
    if not membership or membership.project_id != project.id or membership.tenant_id != user.tenant_id:
        raise HTTPException(404, "项目成员不存在")
    if payload.permission_code not in GRANTABLE_CAPABILITIES:
        raise HTTPException(422, "该权限不允许临时授予")
    now = datetime.now(UTC)
    expires_at = payload.expires_at if payload.expires_at.tzinfo else payload.expires_at.replace(tzinfo=UTC)
    if expires_at < now + timedelta(hours=1) or expires_at > now + timedelta(days=30):
        raise HTTPException(422, "临时授权有效期必须为 1 小时至 30 天")
    existing = await session.scalar(select(ProjectCapabilityGrant).where(
        ProjectCapabilityGrant.membership_id == membership.id,
        ProjectCapabilityGrant.permission_code == payload.permission_code,
        ProjectCapabilityGrant.revoked_at.is_(None),
    ))
    if existing:
        old_expiry = existing.expires_at if existing.expires_at.tzinfo else existing.expires_at.replace(tzinfo=UTC)
        if old_expiry > now:
            raise HTTPException(409, "该成员已有有效的同类临时授权")
        existing.revoked_at = now
        existing.revoked_by = user.user_id
        await session.flush()
    item = ProjectCapabilityGrant(
        membership_id=membership.id,
        permission_code=payload.permission_code,
        reason=payload.reason.strip(),
        granted_by=user.user_id,
        granted_at=now,
        expires_at=expires_at,
    )
    session.add(item)
    await session.flush()
    session.add(audit_event(request, user, "project.capability_granted", item.id, _grant_payload(item)))
    await session.commit()
    return {"data": _grant_payload(item)}


@router.delete("/projects/{project_id}/members/{membership_id}/capability-grants/{grant_id}")
async def revoke_capability_grant(
    project_id: UUID,
    membership_id: UUID,
    grant_id: UUID,
    request: Request,
    _: str = Depends(idempotency_key),
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    if user.company_role_code != "company_admin":
        raise HTTPException(403, "只有公司管理员可以撤销临时能力")
    project = await accessible_project_or_404(session, str(project_id), user)
    membership = await session.get(ProjectMembership, str(membership_id))
    item = await session.get(ProjectCapabilityGrant, str(grant_id))
    if not membership or membership.project_id != project.id or membership.tenant_id != user.tenant_id:
        raise HTTPException(404, "项目成员不存在")
    if not item or item.membership_id != membership.id:
        raise HTTPException(404, "临时授权不存在")
    if not item.revoked_at:
        item.revoked_at, item.revoked_by = datetime.now(UTC), user.user_id
        session.add(audit_event(request, user, "project.capability_revoked", item.id, {
            "project_id": project.id, "permission_code": item.permission_code,
        }))
        await session.commit()
    return {"data": _grant_payload(item)}


@router.get("/projects/{project_id}/collaborating-companies")
async def list_collaborations(
    project_id: UUID,
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    project = await accessible_project_or_404(session, str(project_id), user)
    await require_project_permission(session, project, user, "collaboration.view")
    rows = (await session.execute(
        select(ProjectCollaboration, Tenant)
        .join(Tenant, Tenant.id == ProjectCollaboration.tenant_id)
        .where(ProjectCollaboration.project_id == project.id)
        .order_by(ProjectCollaboration.created_at.desc())
    )).all()
    return {"data": [{
        "id": item.id, "tenant_id": tenant.id, "tenant_name": tenant.name,
        "status": item.status, "created_at": item.created_at.isoformat(),
        "accepted_at": item.accepted_at.isoformat() if item.accepted_at else None,
        "revoked_at": item.revoked_at.isoformat() if item.revoked_at else None,
    } for item, tenant in rows]}


@router.post("/projects/{project_id}/collaborating-companies")
async def invite_collaborating_company(
    project_id: UUID,
    payload: ProjectCollaborationCreateRequest,
    request: Request,
    _: str = Depends(idempotency_key),
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    project = await accessible_project_or_404(session, str(project_id), user)
    await require_project_permission(session, project, user, "collaboration.invite")
    target_id = str(payload.tenant_id)
    if target_id == project.tenant_id:
        raise HTTPException(409, "项目主归属公司不能作为协作公司")
    tenant = await session.get(Tenant, target_id)
    if not tenant or tenant.status != "active" or tenant.deleted_at is not None:
        raise HTTPException(404, "合作公司不存在")
    item = await session.scalar(select(ProjectCollaboration).where(
        ProjectCollaboration.project_id == project.id,
        ProjectCollaboration.tenant_id == target_id,
    ))
    if item:
        item.status, item.invited_by = "pending", user.user_id
        item.accepted_by = item.accepted_at = item.revoked_by = item.revoked_at = None
        item.updated_at = datetime.now(UTC)
    else:
        item = ProjectCollaboration(
            project_id=project.id, owner_tenant_id=project.tenant_id,
            tenant_id=target_id, invited_by=user.user_id,
        )
        session.add(item)
        await session.flush()
    session.add(audit_event(request, user, "project.collaboration_invited", item.id, {
        "project_id": project.id, "tenant_id": target_id,
    }))
    await session.commit()
    return {"data": {"id": item.id, "status": item.status}}


async def _decide_collaboration(
    collaboration_id: str, decision: str, request: Request, user: Principal, session: AsyncSession
):
    if user.company_role_code != "company_admin":
        raise HTTPException(403, "只有公司管理员可以处理协作邀请")
    item = await session.get(ProjectCollaboration, collaboration_id)
    if not item or item.tenant_id != user.tenant_id:
        raise HTTPException(404, "协作邀请不存在")
    if item.status != "pending":
        raise HTTPException(409, "协作邀请已处理")
    item.status, item.updated_at = decision, datetime.now(UTC)
    if decision == "active":
        item.accepted_by, item.accepted_at = user.user_id, datetime.now(UTC)
    session.add(audit_event(request, user, f"project.collaboration_{decision}", item.id, {
        "project_id": item.project_id, "tenant_id": item.tenant_id,
    }))
    await session.commit()
    return {"data": {"id": item.id, "status": item.status}}


@router.post("/project-collaborations/{collaboration_id}/accept")
async def accept_collaboration(collaboration_id: UUID, request: Request, _: str = Depends(idempotency_key), user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    return await _decide_collaboration(str(collaboration_id), "active", request, user, session)


@router.post("/project-collaborations/{collaboration_id}/reject")
async def reject_collaboration(collaboration_id: UUID, request: Request, _: str = Depends(idempotency_key), user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    return await _decide_collaboration(str(collaboration_id), "rejected", request, user, session)


@router.delete("/project-collaborations/{collaboration_id}")
async def revoke_collaboration(collaboration_id: UUID, request: Request, _: str = Depends(idempotency_key), user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    item = await session.get(ProjectCollaboration, str(collaboration_id))
    if not item:
        raise HTTPException(404, "协作记录不存在")
    project = await accessible_project_or_404(session, item.project_id, user)
    await require_project_permission(session, project, user, "collaboration.revoke")
    item.status, item.revoked_by, item.revoked_at = "revoked", user.user_id, datetime.now(UTC)
    item.updated_at = datetime.now(UTC)
    session.add(audit_event(request, user, "project.collaboration_revoked", item.id, {
        "project_id": item.project_id, "tenant_id": item.tenant_id,
    }))
    await session.commit()
    return {"data": {"revoked": True, "id": item.id}}
