from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.audit import audit_event
from backend.config import get_settings
from backend.db import get_session
from backend.intelligence import embed
from backend.models import KnowledgeDocument
from backend.rag import _tokens, search
from backend.security import Principal, current_principal, require_company_admin

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


async def retrieve(session: AsyncSession, tenant_id: str, query: str, limit: int = 5) -> list[dict]:
    docs = (await session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == tenant_id))).all()
    if not docs:
        return search(query, limit=limit) if get_settings().model_mode == "deterministic" else []
    # Only latest active version of each title may become evidence.
    latest: dict[str, KnowledgeDocument] = {}
    for item in docs:
        if item.title not in latest or latest[item.title].version < item.version:
            latest[item.title] = item
    latest = {title: item for title, item in latest.items() if item.active}
    tokens = _tokens(query)
    lexical = sorted(latest.values(), key=lambda d: (-len(tokens & _tokens(d.title + d.body)), d.id))
    scores = {d.id: 1 / (60 + rank) for rank, d in enumerate(lexical, 1) if tokens & _tokens(d.title + d.body)}
    if session.bind and session.bind.dialect.name == "postgresql":
        fts = (await session.execute(text("SELECT id FROM knowledge_documents WHERE tenant_id=:tenant AND active AND to_tsvector('simple', title || ' ' || body) @@ plainto_tsquery('simple', :query) ORDER BY ts_rank(to_tsvector('simple', title || ' ' || body), plainto_tsquery('simple', :query)) DESC LIMIT 30"), {"tenant": tenant_id, "query": query})).scalars().all()
        vector = await embed(query)
        if vector:
            vector_ids = (await session.execute(text("SELECT id FROM knowledge_documents WHERE tenant_id=:tenant AND active AND embedding IS NOT NULL AND embedding_model=:model ORDER BY CAST(embedding::text AS vector) <=> CAST(:vector AS vector) LIMIT 30"), {"tenant": tenant_id, "model": get_settings().embedding_model, "vector": json.dumps(vector)})).scalars().all()
        else:
            vector_ids = []
        for ranking in (fts, vector_ids):
            for rank, identifier in enumerate(ranking, 1):
                scores[identifier] = scores.get(identifier, 0) + 1 / (60 + rank)
    ranked = sorted(latest.values(), key=lambda d: (-scores.get(d.id, 0), d.id))
    return [{"id": d.id, "title": d.title, "module": d.module, "text": d.body, "version": d.version, "source": d.source, "score": scores[d.id]} for d in ranked if scores.get(d.id, 0) > 0][:limit]


class KnowledgeUpload(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    version: int = Field(ge=1)
    module: str = Field(min_length=2, max_length=60)
    source: str = Field(min_length=3, max_length=500)
    license: str = Field(min_length=3, max_length=500)
    body: str = Field(min_length=20, max_length=30000)


@router.get("")
async def list_documents(user: Principal = Depends(current_principal), session: AsyncSession = Depends(get_session)):
    rows = (await session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == user.tenant_id).order_by(KnowledgeDocument.created_at.desc()))).all()
    return {"data": [{"id": d.id, "title": d.title, "version": d.version, "module": d.module, "source": d.source, "license": d.license, "active": d.active} for d in rows]}


@router.post("")
async def upload_document(payload: KnowledgeUpload, request: Request, user: Principal = Depends(require_company_admin), session: AsyncSession = Depends(get_session)):
    # Tenant row serializes concurrent version publication.
    from backend.models import Tenant
    await session.execute(select(Tenant.id).where(Tenant.id == user.tenant_id).with_for_update())
    previous = await session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.tenant_id == user.tenant_id, KnowledgeDocument.title == payload.title).order_by(KnowledgeDocument.version.desc()))
    if previous and payload.version <= previous.version:
        raise HTTPException(409, "知识版本必须递增")
    item = KnowledgeDocument(tenant_id=user.tenant_id, **payload.model_dump(), embedding=await embed(payload.body), embedding_model=get_settings().embedding_model)
    session.add(item)
    await session.flush()
    session.add(audit_event(request, user, "knowledge.uploaded", item.id, {"title": item.title, "version": item.version}))
    await session.commit()
    return {"data": {"id": item.id}}


@router.post("/{document_id}/deactivate")
async def deactivate(document_id: UUID, request: Request, user: Principal = Depends(require_company_admin), session: AsyncSession = Depends(get_session)):
    item = await session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == str(document_id), KnowledgeDocument.tenant_id == user.tenant_id))
    if not item:
        raise HTTPException(404, "资料不存在")
    item.active = False
    session.add(audit_event(request, user, "knowledge.deactivated", item.id, {}))
    await session.commit()
    return {"data": {"id": item.id, "active": False}}
