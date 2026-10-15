"""Signed-in devices. Every login creates a session; its id travels inside the access token."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import User, UserSession
from app.security import create_access_token
from app.services import audit


def start(db: Session, user: User, method: str) -> str:
    """Create a session for ``user`` and return an access token bound to it."""
    session = UserSession(
        id=str(uuid.uuid4()), user_id=user.id, org_id=user.org_id, method=method,
        ip=audit.client_ip.get(), user_agent=(audit.user_agent.get() or "")[:300] or None,
    )
    db.add(session)
    db.commit()
    return create_access_token(user.id, user.org_id, session.id)


def revoke_all(db: Session, user_id: int, *, except_id: str | None = None) -> int:
    query = update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    if except_id:
        query = query.where(UserSession.id != except_id)
    result = db.execute(query.values(revoked_at=datetime.now(timezone.utc)))
    db.commit()
    return result.rowcount or 0
