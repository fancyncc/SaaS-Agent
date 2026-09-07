from backend.evaluation import run_fixed_evaluation


def test_fixed_dataset_is_valid_and_large_enough():
    result = run_fixed_evaluation()
    assert result["dataset_size"] >= 50
    assert result["schema_valid"]
    assert result["duplicate_ids"] == 0
    assert len(result["categories"]) >= 8
    assert len(result["dataset_checksum"]) == 64
    assert len(result["corpus_checksum"]) == 64
    assert result["mode"] == "deterministic_retrieval"


async def test_platform_evaluation_history_idempotency_and_isolation(client, platform_client):
    from uuid import uuid4

    from backend.db import SessionLocal
    from backend.models import EvaluationRun

    headers = {"Idempotency-Key": str(uuid4())}
    first = await platform_client.post("/api/platform/evaluations/run", headers=headers)
    assert first.status_code == 200, first.text
    repeated = await platform_client.post("/api/platform/evaluations/run", headers=headers)
    assert repeated.json() == first.json()
    assert (await platform_client.post("/api/platform/evaluations/run")).status_code == 400
    tenant_id = (await client.get("/api/auth/me")).json()["data"]["tenant_id"]
    async with SessionLocal() as session:
        private = EvaluationRun(tenant_id=tenant_id, dataset_size="1", result={"private": "客户专有结果"})
        session.add(private)
        await session.commit()
        private_id = private.id
    history = (await platform_client.get("/api/platform/evaluations")).json()["data"]
    assert len(history) == 1
    assert history[0]["id"] == first.json()["data"]["id"]
    downloaded = await platform_client.get(f"/api/platform/evaluations/{history[0]['id']}/download")
    assert downloaded.status_code == 200
    assert downloaded.json()["result"]["dataset_checksum"] == first.json()["data"]["dataset_checksum"]
    assert (await platform_client.get(f"/api/platform/evaluations/{private_id}/download")).status_code == 404
    assert (await client.get("/api/platform/evaluations")).status_code == 403
    assert (await client.get(f"/api/platform/evaluations/{history[0]['id']}/download")).status_code == 403
    assert (await client.post("/api/platform/evaluations/run", headers=headers)).status_code == 403


async def test_auditor_can_read_but_not_execute_evaluation(platform_client):
    from uuid import uuid4

    from sqlalchemy import select

    from backend.db import SessionLocal
    from backend.models import PlatformRoleBinding

    user_id = (await platform_client.get("/api/auth/me")).json()["data"]["id"]
    async with SessionLocal() as session:
        binding = await session.scalar(select(PlatformRoleBinding).where(PlatformRoleBinding.user_id == user_id))
        binding.role_code = "platform_auditor"
        await session.commit()
    assert (await platform_client.get("/api/platform/evaluations")).status_code == 200
    assert (await platform_client.post("/api/platform/evaluations/run", headers={"Idempotency-Key": str(uuid4())})).status_code == 403
