from __future__ import annotations

import hashlib

from fastapi import Request

from backend.models import AuditEvent
from backend.security import Principal


def audit_event(
    request: Request,
    user: Principal,
    event_type: str,
    resource_id: str,
    payload: dict | None = None,
    outcome: str = "success",
) -> AuditEvent:
    ip = request.client.host if request.client else "unknown"
    return AuditEvent(
        tenant_id=user.tenant_id,
        event_type=event_type,
        actor=user.email,
        actor_user_id=user.user_id,
        resource_id=str(resource_id),
        request_id=getattr(request.state, "request_id", ""),
        ip_hash=hashlib.sha256(ip.encode()).hexdigest(),
        outcome=outcome,
        payload=payload or {},
    )
