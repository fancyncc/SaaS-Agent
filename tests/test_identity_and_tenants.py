from __future__ import annotations

from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from backend.main import app


def csrf(client: AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("saas_csrf"), "Idempotency-Key": str(uuid4())}


def invitation_token(url: str) -> str:
    return parse_qs(urlparse(url).query)["token"][0]


async def test_anonymous_business_access_is_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anonymous:
        assert (await anonymous.get("/api/projects")).status_code == 401


async def test_invitation_is_single_use_and_creates_membership(client):
    tenant_id = (await client.get("/api/auth/me")).json()["data"]["tenant_id"]
    response = await client.post(
        "/api/company/invitations",
        headers=csrf(client),
        json={"email": "consultant@example.com", "display_name": "实施顾问", "company_role": "tenant_member", "tenant_id": tenant_id},
    )
    assert response.status_code == 200
    invitation_id = response.json()["data"]["id"]
    invitation = next(
        item for item in (await client.get("/api/company/members")).json()["data"]["invitations"]
        if item["id"] == invitation_id
    )
    assert invitation["status"] == "pending"
    assert invitation["invited_by_name"] == "Legacy 公司管理员"
    assert invitation["invited_by_email"] == "company-admin@example.com"
    assert invitation["created_at"]
    assert invitation["accepted_at"] is None
    token = invitation_token(response.json()["data"]["invitation_url"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as invited:
        accepted = await invited.post(
            f"/api/auth/invitations/{token}/accept",
            json={"display_name": "实施顾问", "password": "SecurePass123"},
        )
        assert accepted.status_code == 200
        assert (await invited.get("/api/auth/me")).json()["data"]["role"] == "tenant_member"
        repeated = await invited.post(
            f"/api/auth/invitations/{token}/accept",
            json={"display_name": "实施顾问", "password": "SecurePass123"},
        )
        assert repeated.status_code == 410
    accepted = next(
        item for item in (await client.get("/api/company/members")).json()["data"]["invitations"]
        if item["id"] == invitation_id
    )
    assert accepted["status"] == "accepted"
    assert accepted["accepted_at"]
    assert (await client.delete(f"/api/company/invitations/{invitation_id}", headers=csrf(client))).status_code == 200
    data = (await client.get("/api/company/members")).json()["data"]
    assert invitation_id not in {item["id"] for item in data["invitations"]}
    assert "consultant@example.com" in {member["email"] for member in data["members"]}
    audit = (await client.get("/api/company/audit-events?event_type=invitation.deleted")).json()["data"]
    assert any(item["resource_id"] == invitation_id for item in audit)


async def test_tenant_resource_ids_do_not_cross_boundaries(client, platform_client):
    created = await platform_client.post(
        "/api/platform/tenants",
        headers=csrf(platform_client),
        json={"name": "隔离测试企业", "slug": "isolation-test", "admin_email": "tenant-admin@example.com", "admin_name": "租户管理员"},
    )
    token = invitation_token(created.json()["data"]["invitation_url"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as tenant_client:
        accepted = await tenant_client.post(
            f"/api/auth/invitations/{token}/accept",
            json={"display_name": "租户管理员", "password": "TenantPass123"},
        )
        assert accepted.status_code == 200
        tenant_client.headers["X-CSRF-Token"] = tenant_client.cookies.get("saas_csrf")
        project = await tenant_client.post(
            "/api/projects",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "name": "租户 B 项目", "customer_name": "租户 B", "customer_contact": "联系人",
                "contact_email": "contact@example.com", "employee_count": 10,
                "target_go_live_date": "2030-01-01", "departments": ["市场部"],
                "requirements_text": "市场部需要创建项目、审批任务并导入成员数据。",
            },
        )
        assert project.status_code == 200
        project_id = project.json()["data"]["id"]
    assert (await client.get(f"/api/projects/{project_id}")).status_code == 404


async def test_viewer_cannot_use_admin_or_create_project(client):
    tenant_id = (await client.get("/api/auth/me")).json()["data"]["tenant_id"]
    response = await client.post(
        "/api/company/invitations",
        headers=csrf(client),
        json={"email": "viewer@example.com", "company_role": "tenant_member", "tenant_id": tenant_id},
    )
    token = invitation_token(response.json()["data"]["invitation_url"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as viewer:
        await viewer.post(f"/api/auth/invitations/{token}/accept", json={"display_name": "只读成员", "password": "ViewerPass123"})
        viewer.headers["X-CSRF-Token"] = viewer.cookies.get("saas_csrf")
        assert (await viewer.get("/api/company/members")).status_code == 403
        denied = await viewer.post(
            "/api/projects", headers={"Idempotency-Key": str(uuid4())},
            json={"name": "不可创建", "customer_name": "客户", "customer_contact": "联系人",
                  "contact_email": "x@example.com", "employee_count": 1,
                  "target_go_live_date": "2030-01-01", "departments": ["部门"],
                  "requirements_text": "这是足够长的实施需求描述用于权限测试。"},
        )
        assert denied.status_code == 403


async def test_soft_deleted_project_can_be_restored(client):
    project = await client.post(
        "/api/projects", headers=csrf(client),
        json={"name": "恢复测试项目", "customer_name": "恢复测试客户", "customer_contact": "联系人",
              "contact_email": "restore@example.com", "employee_count": 3,
              "target_go_live_date": "2030-01-01", "departments": ["部门"],
              "requirements_text": "部门成员需要创建任务并由负责人完成审批流程。"},
    )
    project_id = project.json()["data"]["id"]
    assert (await client.delete(f"/api/projects/{project_id}", headers=csrf(client))).status_code == 200
    assert (await client.get(f"/api/projects/{project_id}")).status_code == 404
    trash = (await client.get("/api/company/projects/trash")).json()["data"]
    assert project_id in {item["id"] for item in trash}
    assert (await client.post(f"/api/company/projects/{project_id}/restore", headers=csrf(client))).status_code == 200
    assert (await client.get(f"/api/projects/{project_id}")).status_code == 200


async def test_company_collaboration_and_project_membership_isolate_projects(client, platform_client):
    async def create_company(name: str, slug: str, email: str):
        created = await platform_client.post(
            "/api/platform/tenants", headers=csrf(platform_client),
            json={"name": name, "slug": slug, "admin_email": email, "admin_name": f"{name}管理员"},
        )
        assert created.status_code == 200
        token = invitation_token(created.json()["data"]["invitation_url"])
        company_client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        accepted = await company_client.post(
            f"/api/auth/invitations/{token}/accept",
            json={"display_name": f"{name}管理员", "password": "CompanyPass123"},
        )
        assert accepted.status_code == 200
        company_client.headers["X-CSRF-Token"] = company_client.cookies.get("saas_csrf")
        return created.json()["data"]["id"], company_client

    company_a_id, company_a = await create_company("合作公司A", "company-a", "admin-a@example.com")
    company_b_id, company_b = await create_company("合作公司B", "company-b", "admin-b@example.com")
    try:
        project = await company_a.post(
            "/api/projects", headers={"Idempotency-Key": str(uuid4())},
            json={
                "name": "公司协作隔离项目", "customer_name": "合作公司A", "customer_contact": "联系人",
                "contact_email": "contact-a@example.com", "employee_count": 10,
                "target_go_live_date": "2030-01-01", "departments": ["项目部"],
                "requirements_text": "公司A创建项目，公司B只有接受协作并分配项目角色后才能参与。",
            },
        )
        assert project.status_code == 200
        project_id = project.json()["data"]["id"]
        assert (await company_b.get("/api/projects")).json()["data"] == []

        collaboration = await company_a.post(
            f"/api/projects/{project_id}/collaborating-companies",
            headers={"Idempotency-Key": str(uuid4())}, json={"tenant_id": company_b_id},
        )
        assert collaboration.status_code == 200
        collaboration_id = collaboration.json()["data"]["id"]
        assert (await company_b.get("/api/projects")).json()["data"] == []
        assert (await company_b.post(
            f"/api/project-collaborations/{collaboration_id}/accept",
            headers={"Idempotency-Key": str(uuid4())},
        )).status_code == 200
        assert {p["id"] for p in (await company_b.get("/api/projects")).json()["data"]} == {project_id}

        invitation = await company_b.post(
            "/api/company/invitations", headers={"Idempotency-Key": str(uuid4())},
            json={"tenant_id": company_b_id, "email": "member-b@example.com", "display_name": "B公司成员", "company_role": "tenant_member"},
        )
        token = invitation_token(invitation.json()["data"]["invitation_url"])
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as member_b:
            await member_b.post(
                f"/api/auth/invitations/{token}/accept",
                json={"display_name": "B公司成员", "password": "MemberPass123"},
            )
            member_b.headers["X-CSRF-Token"] = member_b.cookies.get("saas_csrf")
            assert (await member_b.get("/api/projects")).json()["data"] == []
            member_id = (await member_b.get("/api/auth/me")).json()["data"]["id"]

            granted = await company_b.post(
                f"/api/projects/{project_id}/members",
                headers={"Idempotency-Key": str(uuid4())},
                json={"user_id": member_id, "project_role": "viewer"},
            )
            assert granted.status_code == 200
            visible = (await member_b.get("/api/projects")).json()["data"]
            assert {p["id"] for p in visible} == {project_id}
            assert visible[0]["permissions"] == [
                "acceptance.view", "approval.view", "artifact.view", "import.view", "project.view", "run.view"
            ]
            assert (await member_b.post(
                f"/api/projects/{project_id}/runs", headers={"Idempotency-Key": str(uuid4())},
            )).status_code == 403

            assert (await company_a.delete(
                f"/api/project-collaborations/{collaboration_id}",
                headers={"Idempotency-Key": str(uuid4())},
            )).status_code == 200
            assert (await company_b.get(f"/api/projects/{project_id}")).status_code == 404
            assert (await member_b.get(f"/api/projects/{project_id}")).status_code == 404
    finally:
        await company_a.aclose()
        await company_b.aclose()
