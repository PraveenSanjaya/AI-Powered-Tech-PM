from sqlalchemy.orm import Session

from app.core.logging import redact
from app.models import AuditEvent

_SENSITIVE = {"password", "token", "secret", "credentials", "authorization", "api_key", "encrypted_credentials"}


def _scrub(obj):
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if k.lower() in _SENSITIVE else _scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    if isinstance(obj, str):
        return redact(obj)
    return obj


def audit(db: Session, workspace_id: int, event_type: str, *, actor_user_id: int | None = None,
          actor_type: str = "user", target_type: str | None = None, target_id: int | str | None = None,
          payload: dict | None = None) -> AuditEvent:
    ev = AuditEvent(workspace_id=workspace_id, actor_user_id=actor_user_id, actor_type=actor_type,
                    event_type=event_type, target_type=target_type,
                    target_id=str(target_id) if target_id is not None else None,
                    payload=_scrub(payload or {}))
    db.add(ev)
    db.flush()
    return ev
