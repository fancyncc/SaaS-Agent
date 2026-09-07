"""Explicit opt-in check against a disposable migrated PostgreSQL database."""
import asyncio
import os
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def prepare():
    owner_url = os.environ["REGISTRATION_TEST_POSTGRES_URL"]
    # This script deliberately cannot run against a production-named database.
    with psycopg.connect(owner_url, autocommit=True) as connection:
        if connection.info.dbname != "registration_test":
            raise RuntimeError("Use the disposable registration_test database")
        if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname='registration_app'").fetchone():
            connection.execute("CREATE ROLE registration_app LOGIN NOBYPASSRLS NOSUPERUSER")
        connection.execute("GRANT USAGE ON SCHEMA public TO registration_app")
        connection.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO registration_app")
    os.environ["DATABASE_URL"] = owner_url.replace("postgresql://postgres@", "postgresql+psycopg://registration_app@")
    os.environ["MAIL_DEBUG"] = "true"
    os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/15"


async def check():
    from sqlalchemy import func, select, text
    from test_open_registration import (
        anonymous,
        commit,
        headers,
        register,
        test_personal_complete_workflow_and_company_isolation,
        token,
        validate,
    )

    from backend.db import SessionLocal, bootstrap_identity, engine
    from backend.models import CompanyDirectoryEntry, TenantMembership

    await bootstrap_identity()
    async with anonymous() as first, anonymous() as second, anonymous() as target, anonymous() as other_session:
        await register(first, "pg-first@example.com")
        await register(second, "pg-second@example.com")
        target_user = await register(target, "pg-target@example.com")
        companies = []
        for client, slug in ((first, "pg-first"), (second, "pg-second")):
            result = await client.post("/api/companies", headers=headers(client), json={"name": "PG 公司", "slug": slug})
            assert result.status_code == 200, result.text
            company_id = result.json()["data"]["id"]
            companies.append(company_id)
            result = await client.post(f"/api/auth/spaces/{company_id}/switch", headers=headers(client))
            assert result.status_code == 200, result.text
        csv_text = "姓名,邮箱,部门,工号\n个人用户,pg-target@example.com,实施部,001"
        invites = []
        for client in (first, second):
            batch = await validate(client, csv_text)
            result = await commit(client, batch)
            invites.append(token(result["invitations"][0]["invitation_url"]))
        login = await other_session.post("/api/auth/login", json={"email": "pg-target@example.com", "password": "PersonalPass123"})
        assert login.status_code == 200, login.text
        responses = await asyncio.gather(
            target.post(f"/api/auth/invitations/{invites[0]}/accept", headers=headers(target), json={}),
            other_session.post(f"/api/auth/invitations/{invites[1]}/accept", headers=headers(other_session), json={}),
        )
        assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
        async with SessionLocal() as session:
            assert await session.scalar(select(func.count()).select_from(TenantMembership).where(
                TenantMembership.user_id == target_user["id"], TenantMembership.workspace_kind == "company", TenantMembership.status == "active")) == 1
            # The actual application role cannot see another tenant's directory.
            assert await session.scalar(select(func.count()).select_from(CompanyDirectoryEntry)) == 0
            await session.execute(text("SELECT set_config('app.current_tenant_id', :tenant, true)"), {"tenant": companies[0]})
            entries = (await session.scalars(select(CompanyDirectoryEntry))).all()
            assert len(entries) == 1 and entries[0].tenant_id == companies[0]
        await test_personal_complete_workflow_and_company_isolation(first)
    await engine.dispose()
    print("PostgreSQL application-role registration, concurrent company activation, RLS and full personal workflow passed")


if __name__ == "__main__":
    prepare()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(check())
