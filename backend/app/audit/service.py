"""Append-only audit writer. Call from every module on every state transition."""
from sqlalchemy.orm import Session

from app.models import AuditLog


def audit(
    db: Session,
    action: str,
    *,
    user_id: int | None = None,
    actor: str = "user",
    entity: str = "",
    entity_id: int | str = "",
    payload: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            actor=actor,
            action=action,
            entity=entity,
            entity_id=str(entity_id),
            payload=payload or {},
        )
    )
    # caller owns the commit so audit rows share the transaction with the change they record
