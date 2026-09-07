import uuid

from sqlalchemy import select
from test_api import project


async def test_worker_recovers_from_committed_node_without_repeating(client, monkeypatch):
    from backend import worker
    settings = get_settings()
    monkeypatch.setattr(settings, "execution_mode", "worker")
    p = await project(client)
    response = await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid.uuid4())})
    run_id = response.json()["data"]["id"]
    async with SessionLocal() as session:
        event_id = (await session.scalar(select(WorkflowOutbox).where(WorkflowOutbox.run_id == run_id))).id
    original = worker.advance
    calls = 0
    async def fail_after_first(session, run, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ConnectionError("injected transient dependency failure")
        return await original(session, run, **kwargs)
    monkeypatch.setattr(worker, "advance", fail_after_first)
    await process_event(event_id)
    async with SessionLocal() as session:
        event = await session.get(WorkflowOutbox, event_id)
        assert event.attempts == 1 and not event.processed
        assert len((await session.scalars(select(AgentStep).where(AgentStep.run_id == run_id))).all()) == 1
    monkeypatch.setattr(worker, "advance", original)
    await process_event(event_id, raise_errors=True)
    async with SessionLocal() as session:
        assert (await session.get(WorkflowOutbox, event_id)).processed
        assert len((await session.scalars(select(AgentStep).where(AgentStep.run_id == run_id))).all()) == 6

from backend.config import get_settings
from backend.db import SessionLocal
from backend.models import AgentRun, AgentStep, WorkflowOutbox
from backend.worker import process_event


async def test_outbox_advances_and_replay_does_not_duplicate_steps(client):
    settings = get_settings()
    original = settings.execution_mode
    settings.execution_mode = "worker"
    try:
        p = await project(client)
        started = await client.post(f"/api/projects/{p['id']}/runs", headers={"Idempotency-Key": str(uuid.uuid4())})
        assert started.status_code == 200
        run_id = started.json()["data"]["id"]
        async with SessionLocal() as session:
            event = await session.scalar(select(WorkflowOutbox).where(WorkflowOutbox.run_id == run_id))
            event_id = event.id
            assert (await session.get(AgentRun, run_id)).status == "pending"
        await process_event(event_id, raise_errors=True)
        async with SessionLocal() as session:
            event = await session.get(WorkflowOutbox, event_id)
            assert event.processed, event.last_error
            assert (await session.get(AgentRun, run_id)).status == "waiting_approval"
            count = len((await session.scalars(select(AgentStep).where(AgentStep.run_id == run_id))).all())
        await process_event(event_id)
        async with SessionLocal() as session:
            assert len((await session.scalars(select(AgentStep).where(AgentStep.run_id == run_id))).all()) == count == 6
    finally:
        settings.execution_mode = original
