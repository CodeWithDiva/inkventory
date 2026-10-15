"""Team collaboration: comments with @mentions, product assignment and in-app notifications."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import can_edit, get_current_user, get_owned
from app.models import Catalog, Comment, Notification, Product, Role, User
from app.schemas import AssignIn, BulkAssignIn, CommentIn, CommentOut, NotificationOut, ProductOut, ReadNotificationsIn
from app.services import notifications

router = APIRouter(tags=["workflow"])


def _link(product: Product, language: str | None = None) -> str:
    return f"/catalogs/{product.catalog_id}/products/{product.id}" + (f"?lang={language}" if language else "")


# ---------- Comments ----------
@router.get("/products/{product_id}/comments", response_model=list[CommentOut])
def list_comments(product_id: int, language: str | None = None, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    get_owned(db, Product, product_id, user)
    query = select(Comment).where(Comment.product_id == product_id)
    if language:
        query = query.where((Comment.language == language) | Comment.language.is_(None))
    return db.scalars(query.order_by(Comment.id)).all()


@router.post("/products/{product_id}/comments", response_model=CommentOut, status_code=201)
def add_comment(product_id: int, body: CommentIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    product = get_owned(db, Product, product_id, user)
    team = {u.id: u for u in db.scalars(select(User).where(User.org_id == user.org_id, User.is_active.is_(True)))}
    mentions = [uid for uid in dict.fromkeys(body.mentions) if uid in team]
    comment = Comment(org_id=user.org_id, product_id=product.id, language=body.language, version_id=body.version_id,
                      user_id=user.id, author=user.full_name, body=body.body.strip(), mentions=mentions)
    db.add(comment)
    db.commit()
    snippet = comment.body[:200]
    notifications.notify(db, user.org_id, mentions, "mention", f"{user.full_name} mentioned you on {product.name}",
                         snippet, _link(product, body.language), exclude=user.id)
    # The assignee hears about every comment on their product.
    if product.assignee_id and product.assignee_id not in mentions:
        notifications.notify(db, user.org_id, [product.assignee_id], "comment",
                             f"New comment on {product.name}", snippet, _link(product, body.language), exclude=user.id)
    return comment


@router.patch("/comments/{comment_id}", response_model=CommentOut)
def resolve_comment(comment_id: int, resolved: bool = True, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    comment = get_owned(db, Comment, comment_id, user)
    comment.resolved = resolved
    db.commit()
    return comment


@router.delete("/comments/{comment_id}", status_code=204)
def delete_comment(comment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    comment = get_owned(db, Comment, comment_id, user)
    if comment.user_id != user.id and user.role not in (Role.OWNER, Role.ADMIN):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can only delete your own comments")
    db.delete(comment)
    db.commit()


# ---------- Assignment ----------
def _check_assignee(db: Session, user: User, assignee_id: int | None) -> User | None:
    if assignee_id is None:
        return None
    assignee = db.get(User, assignee_id)
    if assignee is None or assignee.org_id != user.org_id or not assignee.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose an active team member")
    return assignee


@router.patch("/products/{product_id}/assign", response_model=ProductOut)
def assign_product(product_id: int, body: AssignIn, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    product = get_owned(db, Product, product_id, user)
    assignee = _check_assignee(db, user, body.user_id)
    product.assignee_id = assignee.id if assignee else None
    db.commit()
    if assignee:
        notifications.notify(db, user.org_id, [assignee.id], "assigned", f"{user.full_name} assigned you {product.name}",
                             "", _link(product), exclude=user.id)
    return product


@router.post("/catalogs/{catalog_id}/assign")
def bulk_assign(catalog_id: int, body: BulkAssignIn, user: User = Depends(can_edit), db: Session = Depends(get_db)):
    catalog = get_owned(db, Catalog, catalog_id, user)
    assignee = _check_assignee(db, user, body.user_id)
    result = db.execute(
        update(Product).where(Product.catalog_id == catalog.id, Product.id.in_(body.product_ids))
        .values(assignee_id=assignee.id if assignee else None)
    )
    db.commit()
    if assignee and result.rowcount:
        notifications.notify(db, user.org_id, [assignee.id], "assigned",
                             f"{user.full_name} assigned you {result.rowcount} products in {catalog.name}",
                             "", f"/catalogs/{catalog.id}?assignee=me", exclude=user.id)
    return {"updated": result.rowcount}


# ---------- Notifications ----------
@router.get("/notifications", response_model=list[NotificationOut])
def list_notifications(unread: bool = False, limit: int = Query(30, le=100), user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    query = select(Notification).where(Notification.user_id == user.id)
    if unread:
        query = query.where(Notification.read_at.is_(None))
    return db.scalars(query.order_by(Notification.id.desc()).limit(limit)).all()


@router.get("/notifications/unread-count")
def unread_count(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    count = db.scalar(select(func.count(Notification.id)).where(
        Notification.user_id == user.id, Notification.read_at.is_(None))) or 0
    return {"count": count}


@router.post("/notifications/read")
def mark_read(body: ReadNotificationsIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
    if body.ids is not None:
        query = query.where(Notification.id.in_(body.ids))
    result = db.execute(query.values(read_at=datetime.now(timezone.utc)))
    db.commit()
    return {"updated": result.rowcount}
