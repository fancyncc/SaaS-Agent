from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.db import get_session
from backend.mailer import send_account_link
from backend.models import (
    AuthSession,
    PasswordResetToken,
    PlatformInvitation,
    PlatformRoleBinding,
    Tenant,
    TenantMembership,
    User,
    UserInvitation,
)
from backend.rate_limit import clear_rate_limit, enforce_rate_limit
from backend.schemas import (
    InvitationAcceptRequest,
    LoginRequest,
    PasswordForgotRequest,
    PasswordResetRequest,
)
from backend.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    Principal,
    create_session,
    current_principal,
    hash_password,
    token_hash,
    validate_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["authentication"])


def _set_auth_cookies(response: Response, raw_session: str, raw_csrf: str) -> None:
    settings = get_settings()
    max_age = settings.session_hours * 3600
    response.set_cookie(
        SESSION_COOKIE,
        raw_session,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        raw_csrf,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


async def _membership_payload(session: AsyncSession, user_id: str) -> list[dict]:
    rows = (
        await session.execute(
            select(TenantMembership, Tenant)
            .join(Tenant, Tenant.id == TenantMembership.tenant_id)
            .where(
                TenantMembership.user_id == user_id,
                TenantMembership.status == "active",
                Tenant.status == "active",
                Tenant.deleted_at.is_(None),
            )
        )
    ).all()
    return [
        {
            "tenant_id": tenant.id,
            "tenant_name": tenant.name,
            "role": membership.role,
            "company_role_code": membership.company_role_code,
        }
        for membership, tenant in rows
    ]


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    email = payload.email.strip().lower()
    ip = request.client.host if request.client else "unknown"
    rate_key = f"login:{ip}:{email}"
    await enforce_rate_limit(rate_key, 8, 900)
    user = await session.scalar(select(User).where(User.email == email, User.deleted_at.is_(None)))
    if (not user or user.status != "active" or user.account_type != "customer"
            or not verify_password(user.password_hash, payload.password)):
        raise HTTPException(401, "邮箱或密码错误")
    await clear_rate_limit(rate_key)
    row = await session.execute(
        select(TenantMembership, Tenant)
        .join(Tenant, Tenant.id == TenantMembership.tenant_id)
        .where(
            TenantMembership.user_id == user.id,
            TenantMembership.status == "active",
            Tenant.status == "active",
            Tenant.deleted_at.is_(None),
        )
        .order_by(TenantMembership.created_at.asc())
        .limit(1)
    )
    membership_pair = row.first()
    if not membership_pair:
        raise HTTPException(403, "账号没有可用的企业成员关系")
    _, tenant = membership_pair
    raw_session, raw_csrf, _ = await create_session(
        session, user, tenant, request.headers.get("user-agent", ""), "customer"
    )
    user.last_login_at = datetime.now(UTC)
    await session.commit()
    _set_auth_cookies(response, raw_session, raw_csrf)
    return {"data": {"logged_in": True, "must_change_password": user.must_change_password}}


@router.post("/platform/login")
async def platform_login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    email = payload.email.strip().lower()
    ip = request.client.host if request.client else "unknown"
    rate_key = f"platform-login:{ip}:{email}"
    await enforce_rate_limit(rate_key, 8, 900)
    user = await session.scalar(select(User).where(User.email == email, User.deleted_at.is_(None)))
    bindings = [] if not user else list((await session.scalars(select(PlatformRoleBinding).where(
        PlatformRoleBinding.user_id == user.id, PlatformRoleBinding.status == "active",
    ))).all())
    if (not user or user.status != "active" or user.account_type != "platform" or not bindings
            or not verify_password(user.password_hash, payload.password)):
        raise HTTPException(401, "平台账号或密码错误")
    await clear_rate_limit(rate_key)
    raw_session, raw_csrf, _ = await create_session(
        session, user, None, request.headers.get("user-agent", ""), "platform"
    )
    user.last_login_at = datetime.now(UTC)
    await session.commit()
    _set_auth_cookies(response, raw_session, raw_csrf)
    return {"data": {"logged_in": True, "must_change_password": user.must_change_password}}


@router.get("/me")
async def me(user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    return {
        "data": {
            "id": user.user_id,
            "email": user.email,
            "display_name": user.display_name,
            "tenant_id": user.tenant_id,
            "tenant_name": user.tenant_name,
            "account_type": user.account_type,
            "session_context": user.session_context,
            "role": user.role.value,
            "is_platform_admin": user.is_platform_admin,
            "company_role_code": user.company_role_code,
            "platform_roles": list(user.platform_roles),
            "memberships": await _membership_payload(session, user.user_id) if user.account_type == "customer" else [],
        }
    }


@router.post("/logout")
async def logout(
    response: Response,
    user: Principal = Depends(current_principal),
    session: AsyncSession = Depends(get_session),
):
    auth = await session.get(AuthSession, user.session_id)
    if auth:
        auth.revoked_at = datetime.now(UTC)
    await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"data": {"logged_out": True}}


@router.post("/invitations/{token}/accept")
async def accept_invitation(
    token: str,
    payload: InvitationAcceptRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    await enforce_rate_limit(f"invite:{request.client.host if request.client else 'unknown'}", 12, 900)
    validate_password(payload.password)
    invitation = await session.scalar(
        select(UserInvitation).where(UserInvitation.token_hash == token_hash(token))
    )
    now = datetime.now(UTC)
    expires_at = None if not invitation else (
        invitation.expires_at.replace(tzinfo=UTC)
        if invitation.expires_at.tzinfo is None
        else invitation.expires_at
    )
    if not invitation or invitation.status != "pending" or not expires_at or expires_at <= now:
        raise HTTPException(410, "邀请无效或已过期")
    tenant = await session.get(Tenant, invitation.tenant_id)
    if not tenant or tenant.status != "active" or tenant.deleted_at is not None:
        raise HTTPException(410, "邀请所属企业不可用")
    user = await session.scalar(select(User).where(User.email == invitation.email))
    if user and user.deleted_at is not None:
        raise HTTPException(409, "该账号不可用，请联系管理员")
    if not user:
        user = User(
            email=invitation.email,
            display_name=payload.display_name.strip(),
            password_hash=hash_password(payload.password),
            account_type="customer",
        )
        session.add(user)
        await session.flush()
    else:
        if user.account_type != "customer":
            raise HTTPException(409, "平台账号不能接受企业邀请")
        other_company = await session.scalar(select(TenantMembership).where(
            TenantMembership.user_id == user.id,
            TenantMembership.tenant_id != invitation.tenant_id,
            TenantMembership.status == "active",
        ))
        if other_company:
            raise HTTPException(409, "一个账号只能属于一家公司，请使用该公司的专用邮箱账号")
        user.display_name = payload.display_name.strip()
        user.password_hash = hash_password(payload.password)
        user.status = "active"
    membership = await session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == invitation.tenant_id,
            TenantMembership.user_id == user.id,
        )
    )
    if membership:
        membership.role = invitation.role
        membership.company_role_code = "company_admin" if invitation.role == "tenant_admin" else "company_member"
        membership.status = "active"
    else:
        session.add(TenantMembership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=invitation.role,
            company_role_code="company_admin" if invitation.role == "tenant_admin" else "company_member",
        ))
    invitation.status = "accepted"
    invitation.accepted_at = now
    raw_session, raw_csrf, _ = await create_session(
        session, user, tenant, request.headers.get("user-agent", "")
    )
    await session.commit()
    _set_auth_cookies(response, raw_session, raw_csrf)
    return {"data": {"accepted": True, "tenant_id": tenant.id}}


@router.post("/platform-invitations/{token}/accept")
async def accept_platform_invitation(
    token: str,
    payload: InvitationAcceptRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    await enforce_rate_limit(f"platform-invite:{request.client.host if request.client else 'unknown'}", 12, 900)
    validate_password(payload.password)
    invitation = await session.scalar(select(PlatformInvitation).where(
        PlatformInvitation.token_hash == token_hash(token)
    ))
    now = datetime.now(UTC)
    expiry = None if not invitation else invitation.expires_at
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    if not invitation or invitation.status != "pending" or not expiry or expiry <= now:
        raise HTTPException(410, "平台邀请无效或已过期")
    user = await session.scalar(select(User).where(User.email == invitation.email))
    if user:
        active_membership = await session.scalar(select(TenantMembership).where(
            TenantMembership.user_id == user.id, TenantMembership.status == "active"
        ))
        if user.account_type != "platform" or active_membership:
            raise HTTPException(409, "客户公司账号不能转换为平台账号")
        user.display_name = payload.display_name.strip()
        user.password_hash = hash_password(payload.password)
        user.status = "active"
    else:
        user = User(email=invitation.email, display_name=payload.display_name.strip(),
                    password_hash=hash_password(payload.password), account_type="platform")
        session.add(user)
        await session.flush()
    binding = await session.scalar(select(PlatformRoleBinding).where(
        PlatformRoleBinding.user_id == user.id,
        PlatformRoleBinding.role_code == invitation.role_code,
    ))
    if binding:
        binding.status = "active"
    else:
        session.add(PlatformRoleBinding(user_id=user.id, role_code=invitation.role_code,
                                        granted_by=invitation.invited_by))
    invitation.status = "accepted"
    invitation.accepted_at = now
    raw_session, raw_csrf, _ = await create_session(
        session, user, None, request.headers.get("user-agent", ""), "platform"
    )
    await session.commit()
    _set_auth_cookies(response, raw_session, raw_csrf)
    return {"data": {"accepted": True, "account_type": "platform"}}


@router.post("/password/forgot")
async def forgot_password(
    payload: PasswordForgotRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    email = payload.email.strip().lower()
    await enforce_rate_limit(f"forgot:{request.client.host if request.client else 'unknown'}:{email}", 5, 3600)
    user = await session.scalar(select(User).where(User.email == email, User.deleted_at.is_(None)))
    preview_url = None
    if user and user.status == "active":
        raw = secrets.token_urlsafe(48)
        session.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=token_hash(raw),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await session.commit()
        preview_url = f"{get_settings().frontend_base_url}/reset-password?token={quote(raw)}"
        await send_account_link(user.email, "password-reset", preview_url)
    data = {"message": "如果邮箱存在，重置链接将发送至该邮箱"}
    if get_settings().mail_debug and preview_url:
        data["preview_url"] = preview_url
    return {"data": data}


@router.post("/password/reset")
async def reset_password(
    payload: PasswordResetRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    await enforce_rate_limit(f"reset:{request.client.host if request.client else 'unknown'}", 10, 900)
    validate_password(payload.password)
    row = await session.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash(payload.token),
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > datetime.now(UTC),
        )
    )
    if not row:
        raise HTTPException(410, "重置链接无效或已过期")
    user = await session.get(User, row.user_id)
    if not user or user.deleted_at is not None:
        raise HTTPException(410, "重置链接无效或已过期")
    user.password_hash = hash_password(payload.password)
    user.must_change_password = False
    row.used_at = datetime.now(UTC)
    await session.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await session.commit()
    return {"data": {"reset": True}}
