"""In-app notifications (the bell). Creating one never breaks the action that caused it."""

import logging
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notification, Role, User

log = logging.getLogger(__name__)


def notify(
    db: Session,
    org_id: int,
    user_ids: Iterable[int],
    kind: str,
    title: str,
    body: str = "",
    link: str | None = None,
    *,
    exclude: int | None = None,
    commit: bool = True,
) -> None:
    try:
        ids = {uid for uid in user_ids if uid and uid != exclude}
        if not ids:
            return
        active = db.scalars(select(User.id).where(User.id.in_(ids), User.org_id == org_id, User.is_active.is_(True)))
        for uid in active:
            db.add(Notification(org_id=org_id, user_id=uid, kind=kind, title=title[:300], body=body[:2000], link=link))
        if commit:
            db.commit()
    except Exception:
        log.exception("Could not create notification %s", kind)
        db.rollback()


def admin_ids(db: Session, org_id: int) -> list[int]:
    return list(db.scalars(select(User.id).where(
        User.org_id == org_id, User.is_active.is_(True), User.role.in_([Role.OWNER, Role.ADMIN]))))
