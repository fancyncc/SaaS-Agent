from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import AgentRun, AgentStep, Approval, ImportJob, Project, ProjectDocument


async def purge_expired_projects(session: AsyncSession) -> int:
    """Physical cleanup job; schedule daily in the deployment task runner."""
    cutoff = datetime.now(UTC) - timedelta(days=30)
    projects = (await session.scalars(select(Project).where(
        Project.deleted_at.is_not(None), Project.deleted_at <= cutoff
    ))).all()
    for project in projects:
        run_ids = list(await session.scalars(select(AgentRun.id).where(AgentRun.project_id == project.id)))
        if run_ids:
            await session.execute(delete(Approval).where(Approval.run_id.in_(run_ids)))
            await session.execute(delete(AgentStep).where(AgentStep.run_id.in_(run_ids)))
            await session.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))
        await session.execute(delete(ImportJob).where(ImportJob.project_id == project.id))
        await session.execute(delete(ProjectDocument).where(ProjectDocument.project_id == project.id))
        await session.delete(project)
    await session.commit()
    return len(projects)
