from __future__ import annotations

import csv
import io
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import audit_event
from backend.config import get_settings
from backend.db import get_session
from backend.mailer import send_account_link
from backend.models import (
    CompanyDirectoryEntry,
    CompanyMemberImport,
    IdempotencyRecord,
    Tenant,
    TenantMembership,
    User,
    UserInvitation,
)
from backend.onboarding import commit_identity
from backend.rate_limit import enforce_rate_limit
from backend.security import Principal, idempotency_key, require_enterprise_admin, token_hash

router = APIRouter(prefix="/api/company/member-imports", tags=["company member registration"])
ALIASES = {"姓名": "display_name", "name": "display_name", "邮箱": "email", "email": "email",
           "部门": "department", "department": "department", "工号": "employee_number", "employee_number": "employee_number"}


class ImportRequest(BaseModel):
    csv_text: str = Field(min_length=1, max_length=2_000_000)

    @field_validator("csv_text")
    @classmethod
    def size_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 2_000_000:
            raise ValueError("文件不能超过 2 MB")
        return value


def parse_rows(content: str) -> list[dict]:
    try:
        reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")), strict=True)
        headers = reader.fieldnames or []
        columns = [ALIASES.get(h.strip().lower()) for h in headers]
        if not {"display_name", "email", "department"}.issubset(columns):
            raise HTTPException(422, "缺少姓名、邮箱或部门列")
        known = [c for c in columns if c]
        if len(set(known)) != len(known):
            raise HTTPException(422, "存在重复含义的列")
        rows: list[dict] = []
        for number, source in enumerate(reader, 2):
            if len(rows) >= 1000:
                raise HTTPException(422, "最多导入 1,000 条成员")
            if None in source or any(value is None for value in source.values()):
                raise HTTPException(422, f"第 {number} 行列数不正确")
            row = {target: source[header].strip() for header, target in zip(headers, columns, strict=True) if target}
            row["email"] = row["email"].lower()
            row["employee_number"] = row.get("employee_number") or None
            rows.append({"row": number, **row})
        if not rows:
            raise HTTPException(422, "文件没有成员记录")
        return rows
    except csv.Error as exc:
        raise HTTPException(422, "CSV 格式无效") from exc


async def validate_rows(session: AsyncSession, tenant_id: str, rows: list[dict]) -> dict:
    entries = list((await session.scalars(select(CompanyDirectoryEntry).where(CompanyDirectoryEntry.tenant_id == tenant_id))).all())
    by_email = {entry.email: entry for entry in entries}
    by_number = {entry.employee_number: entry.email for entry in entries if entry.employee_number}
    pending = {item.email: item for item in (await session.scalars(select(UserInvitation).where(
        UserInvitation.tenant_id == tenant_id, UserInvitation.status == "pending"))).all()}
    users = {user.email: user for user in (await session.scalars(select(User).where(
        User.email.in_([row["email"] for row in rows])))).all()}
    memberships = list((await session.scalars(select(TenantMembership).where(
        TenantMembership.user_id.in_([user.id for user in users.values()]),
        TenantMembership.workspace_kind == "company"))).all())
    current_members = {m.user_id: m for m in memberships if m.tenant_id == tenant_id}
    active_companies = {m.user_id: m for m in memberships if m.status == "active"}
    seen_emails: dict[str, int] = {}
    seen_numbers: dict[str, int] = {}
    results = []
    for row in rows:
        email, number = row["email"], row["employee_number"]
        errors = []
        for field, limit in (("display_name", 100), ("email", 160), ("department", 100)):
            if not row[field] or len(row[field]) > limit:
                errors.append(f"{field} 不能为空且不能超过 {limit} 字符")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            errors.append("邮箱格式无效")
        if number and len(number) > 80:
            errors.append("工号不能超过 80 字符")
        if email in seen_emails:
            errors.append(f"邮箱与第 {seen_emails[email]} 行重复")
        seen_emails[email] = row["row"]
        if number:
            if number in seen_numbers:
                errors.append(f"工号与第 {seen_numbers[number]} 行重复")
            seen_numbers[number] = row["row"]
            if number in by_number and by_number[number] != email:
                errors.append("工号已被占用")
        user = users.get(email)
        membership = None
        if user:
            if user.account_type != "customer" or user.status != "active" or user.deleted_at:
                errors.append("该账号不可加入")
            company = active_companies.get(user.id)
            if company and company.tenant_id != tenant_id:
                errors.append("该账号不可加入")
            membership = current_members.get(user.id)
        entry = by_email.get(email)
        if entry and any(getattr(entry, field) != row[field] for field in ("display_name", "department", "employee_number")):
            errors.append("与已有成员或待激活资料冲突，请在成员管理中处理")
        elif not entry and (membership or email in pending):
            existing_name = user.display_name if membership and user else pending[email].display_name
            if existing_name and existing_name != row["display_name"]:
                errors.append("与已有姓名冲突")
            if number:
                errors.append("已有记录未设置工号，不能通过重复导入覆盖")
        skip = membership is not None or email in pending
        results.append({**row, "status": "error" if errors else "skip" if skip else "new", "errors": errors})
    return {"valid": not any(r["errors"] for r in results), "rows": results,
            "new_count": sum(r["status"] == "new" for r in results),
            "skip_count": sum(r["status"] == "skip" for r in results)}


@router.get("/template.csv")
async def template(_: Principal = Depends(require_enterprise_admin)):
    return Response("\ufeff姓名,邮箱,部门,工号\n张三,zhangsan@example.com,实施部,001\n",
                    media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="members.csv"'})


@router.post("/validate")
async def validate(payload: ImportRequest, user: Principal = Depends(require_enterprise_admin), session: AsyncSession = Depends(get_session)):
    assert user.tenant_id is not None
    await enforce_rate_limit(f"member-validate:{user.tenant_id}", 30, 3600)
    rows = parse_rows(payload.csv_text)
    result = await validate_rows(session, user.tenant_id, rows)
    batch = CompanyMemberImport(tenant_id=user.tenant_id, created_by=user.user_id, rows=rows, result=result,
                                status="validated" if result["valid"] else "invalid")
    session.add(batch)
    await commit_identity(session)
    return {"data": {"id": batch.id, **result}}


@router.get("/{batch_id}")
async def get_batch(batch_id: UUID, user: Principal = Depends(require_enterprise_admin), session: AsyncSession = Depends(get_session)):
    batch = await session.get(CompanyMemberImport, str(batch_id))
    if not batch or batch.tenant_id != user.tenant_id:
        raise HTTPException(404, "导入批次不存在")
    return {"data": {"id": batch.id, "status": batch.status, **batch.result}}


@router.post("/{batch_id}/commit")
async def commit_batch(batch_id: UUID, request: Request, key: str = Depends(idempotency_key),
                       user: Principal = Depends(require_enterprise_admin), session: AsyncSession = Depends(get_session)):
    assert user.tenant_id is not None
    await enforce_rate_limit(f"member-commit:{user.tenant_id}", 30, 3600)
    await session.scalar(select(Tenant).where(Tenant.id == user.tenant_id).with_for_update())
    scope = f"member_import:{batch_id}"
    existing = await session.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.tenant_id == user.tenant_id, IdempotencyRecord.scope == scope, IdempotencyRecord.key == key))
    if existing:
        return {"data": existing.response}
    batch = await session.scalar(select(CompanyMemberImport).where(CompanyMemberImport.id == str(batch_id)).with_for_update())
    if not batch or batch.tenant_id != user.tenant_id:
        raise HTTPException(404, "导入批次不存在")
    if batch.status == "committed":
        return {"data": batch.result}
    result = await validate_rows(session, user.tenant_id, batch.rows)
    if not result["valid"]:
        raise HTTPException(409, detail={"message": "校验未通过，整批未提交", **result})
    invitations = []
    try:
        for row in result["rows"]:
            if row["status"] != "new":
                continue
            entry = await session.scalar(select(CompanyDirectoryEntry).where(
                CompanyDirectoryEntry.tenant_id == user.tenant_id, CompanyDirectoryEntry.email == row["email"]))
            if not entry:
                session.add(CompanyDirectoryEntry(tenant_id=user.tenant_id, **{field: row[field] for field in
                    ("email", "display_name", "department", "employee_number")}))
            raw = secrets.token_urlsafe(48)
            item = UserInvitation(tenant_id=user.tenant_id, email=row["email"], display_name=row["display_name"],
                role="tenant_member", token_hash=token_hash(raw), invited_by=user.user_id,
                expires_at=datetime.now(UTC) + timedelta(hours=48))
            session.add(item)
            await session.flush()
            url = f"{get_settings().frontend_base_url}/accept-invitation?token={raw}"
            await send_account_link(item.email, "invitation", url, session=session)
            invitations.append({"id": item.id, "email": item.email,
                                **({"invitation_url": url} if get_settings().mail_debug else {})})
        # Do not persist raw activation tokens in import or idempotency records.
        data = {"id": batch.id, **result, "invitations": [{"id": i["id"], "email": i["email"]} for i in invitations]}
        batch.status, batch.result = "committed", data
        session.add(IdempotencyRecord(tenant_id=user.tenant_id, scope=scope, key=key, response=data))
        session.add(audit_event(request, user, "members.bulk_registered", batch.id,
                                {"created": result["new_count"], "skipped": result["skip_count"]}))
        await commit_identity(session)
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "成员信息发生并发冲突，请重新校验") from exc
    return {"data": {**data, "invitations": invitations}}
