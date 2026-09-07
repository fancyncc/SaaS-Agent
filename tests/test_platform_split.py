from __future__ import annotations

from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from backend.db import SessionLocal
from backend.main import app
from backend.models import PlatformRoleBinding, ProjectMembership, TenantMembership, User
from backend.security import hash_password


def csrf(client: AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("saas_csrf") or "",
            "Idempotency-Key": str(uuid4())}


def token(url: str) -> str:
    return parse_qs(urlparse(url).query)["token"][0]


def project_body(name: str) -> dict:
    return {
        "name": name, "customer_name": "Legacy Demo", "customer_contact": "联系人",
        "contact_email": "contact@example.com", "employee_count": 10,
        "target_go_live_date": "2030-01-01", "departments": ["项目部"],
        "requirements_text": "项目部成员需要创建任务并由负责人完成审批和上线验收。",
    }


async def test_separate_login_entrypoints_and_removed_admin_api(client, platform_client):
    assert (await platform_client.get("/api/auth/me")).json()["data"]["session_context"] == "platform"
    assert (await client.get("/api/auth/me")).json()["data"]["session_context"] == "customer"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anonymous:
        wrong_platform = await anonymous.post(
            "/api/auth/platform/login",
            json={"email": "company-admin@example.com", "password": "CompanyAdmin123"},
        )
        wrong_customer = await anonymous.post(
            "/api/auth/login", json={"email": "admin@example.com", "password": "ChangeMe123!"},
        )
        assert wrong_platform.status_code == 401
        assert wrong_customer.status_code == 401
    assert (await platform_client.get("/api/admin/dashboard")).status_code == 404
    assert (await client.get("/api/platform/dashboard")).status_code == 403
    assert (await platform_client.get("/api/company/members")).status_code == 403


async def test_super_admin_direct_inspection_is_read_only(client, platform_client):
    project = (await client.post(
        "/api/projects", headers=csrf(client), json=project_body("平台只读检查项目")
    )).json()["data"]
    tenant_id = (await client.get("/api/auth/me")).json()["data"]["tenant_id"]
    projects = await platform_client.get(f"/api/platform/inspect/tenants/{tenant_id}/projects")
    assert projects.status_code == 200
    assert project["id"] in {item["id"] for item in projects.json()["data"]}
    inspected = await platform_client.get(f"/api/platform/inspect/projects/{project['id']}")
    assert inspected.status_code == 200
    assert inspected.json()["data"]["requirements_text"]
    assert (await platform_client.post(
        f"/api/projects/{project['id']}/runs", headers=csrf(platform_client)
    )).status_code in {403, 404}


async def test_company_transfer_revokes_session_and_project_access(client, platform_client):
    invited = await client.post(
        "/api/company/invitations", headers=csrf(client),
        json={"email": "move-me@example.com", "company_role": "tenant_member"},
    )
    member = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await member.post(
            f"/api/auth/invitations/{token(invited.json()['data']['invitation_url'])}/accept",
            json={"display_name": "待转移成员", "password": "MoveMember123"},
        )
        member.headers["X-CSRF-Token"] = member.cookies.get("saas_csrf")
        member_id = (await member.get("/api/auth/me")).json()["data"]["id"]
        project = (await client.post(
            "/api/projects", headers=csrf(client), json=project_body("转移权限回收项目")
        )).json()["data"]
        assigned = await client.post(
            f"/api/projects/{project['id']}/members", headers=csrf(client),
            json={"user_id": member_id, "primary_role_code": "viewer"},
        )
        assert assigned.status_code == 200
        target = await platform_client.post(
            "/api/platform/tenants", headers=csrf(platform_client),
            json={"name": "目标公司", "slug": "transfer-target",
                  "admin_email": "target-owner@example.com", "admin_name": "目标管理员"},
        )
        transferred = await platform_client.post(
            f"/api/platform/users/{member_id}/transfer-company", headers=csrf(platform_client),
            json={"target_tenant_id": target.json()["data"]["id"],
                  "company_role": "tenant_member", "reason": "修正邀请时选择错误的公司"},
        )
        assert transferred.status_code == 200, transferred.text
        assert (await member.get("/api/auth/me")).status_code == 401
        async with SessionLocal() as session:
            memberships = list((await session.scalars(select(TenantMembership).where(
                TenantMembership.user_id == member_id
            ))).all())
            assert len([item for item in memberships if item.status == "active" and item.workspace_kind == "company"]) == 1
            assert len([item for item in memberships if item.status == "active" and item.workspace_kind == "personal"]) == 1
            project_membership = await session.scalar(select(ProjectMembership).where(
                ProjectMembership.user_id == member_id, ProjectMembership.project_id == project["id"]
            ))
            assert project_membership.status == "disabled"
    finally:
        await member.aclose()


async def test_last_company_admin_cannot_be_transferred(client, platform_client):
    me = (await client.get("/api/auth/me")).json()["data"]
    target = await platform_client.post(
        "/api/platform/tenants", headers=csrf(platform_client),
        json={"name": "管理员保护目标", "slug": "last-admin-target",
              "admin_email": "last-target@example.com", "admin_name": "目标管理员"},
    )
    response = await platform_client.post(
        f"/api/platform/users/{me['id']}/transfer-company", headers=csrf(platform_client),
        json={"target_tenant_id": target.json()["data"]["id"],
              "company_role": "tenant_admin", "reason": "验证最后管理员保护规则"},
    )
    assert response.status_code == 409


async def test_platform_role_matrix_blocks_operator_from_super_admin_features():
    async with SessionLocal() as session:
        user = User(email="operator@example.com", display_name="平台运营",
                    password_hash=hash_password("OperatorPass123"), account_type="platform")
        session.add(user)
        await session.flush()
        session.add(PlatformRoleBinding(user_id=user.id, role_code="platform_operator", granted_by=user.id))
        await session.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as operator:
        login = await operator.post(
            "/api/auth/platform/login",
            json={"email": "operator@example.com", "password": "OperatorPass123"},
        )
        assert login.status_code == 200
        assert (await operator.get("/api/platform/users")).status_code == 200
        assert (await operator.get("/api/platform/staff")).status_code == 403
        tenant_id = (await operator.get("/api/platform/tenants")).json()["data"][0]["id"]
        assert (await operator.get(f"/api/platform/inspect/tenants/{tenant_id}/projects")).status_code == 403
