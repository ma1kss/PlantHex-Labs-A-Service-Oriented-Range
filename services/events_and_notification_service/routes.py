from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from cyber_range_core import UserContext, get_logger

import schemas
from db import Announcement, get_sessionmaker
from security import require_auth_local, require_role


EDITOR_ROLES = ("admin", "instructor")

log    = get_logger(__name__)
router = APIRouter()


# Announcements

def _out(a: Announcement) -> schemas.AnnouncementOut:
    return schemas.AnnouncementOut(
        id=a.id, title=a.title, body=a.body,
        author_id=a.author_id, author_name=a.author_name, author_role=a.author_role,
        created_at=a.created_at, updated_at=a.updated_at,
    )


@router.get("/announcements",
            response_model=list[schemas.AnnouncementOut],
            summary="List announcements (any signed-in user)")
async def list_announcements(_: UserContext = Depends(require_auth_local)):
    async with get_sessionmaker()() as s:
        rows = (await s.execute(
            select(Announcement).order_by(Announcement.created_at.desc())
        )).scalars().all()
    return [_out(a) for a in rows]


@router.post("/announcements",
             status_code=status.HTTP_201_CREATED,
             response_model=schemas.AnnouncementOut,
             summary="Publish a new announcement (admin / instructor)")
async def create_announcement(body: schemas.AnnouncementIn,
                              request: Request,
                              user: UserContext = Depends(require_role(*EDITOR_ROLES))):
    primary_role = next((r for r in EDITOR_ROLES if r in user.roles), user.roles[0] if user.roles else "")
    ann = Announcement(
        title=body.title.strip(),
        body=body.body or "",
        author_id=user.id,
        author_name=user.username or "",
        author_role=primary_role,
    )
    async with get_sessionmaker()() as s:
        s.add(ann)
        await s.commit()
        await s.refresh(ann)
        out = _out(ann)

    # Publish on the bus: the WS consumer here fans it out to every connected
    # UI (bell tick), and the Data & Logging consumer captures it for history.
    payload = {
        "service":   "events-service",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "announcement_id":  out.id,
        "title":            out.title,
        "author":           out.author_name,
        "author_role":      out.author_role,
    }
    await request.app.state.publisher.publish(
        "announcement.created", payload,
        correlation_id=request.state.correlation_id,
    )
    log.info("events.announcement.created",
             announcement_id=out.id, author=out.author_name)
    return out


@router.put("/announcements/{ann_id}",
            response_model=schemas.AnnouncementOut,
            summary="Edit an announcement (admin / instructor)")
async def update_announcement(ann_id: str,
                              body: schemas.AnnouncementIn,
                              request: Request,
                              user: UserContext = Depends(require_role(*EDITOR_ROLES))):
    async with get_sessionmaker()() as s:
        a = await s.get(Announcement, ann_id)
        if a is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Announcement not found")
        a.title = body.title.strip()
        a.body  = body.body or ""
        await s.commit()
        await s.refresh(a)
        out = _out(a)

    await request.app.state.publisher.publish(
        "announcement.updated",
        {"service": "events-service",
         "timestamp": datetime.now(timezone.utc).isoformat(),
         "announcement_id": out.id, "title": out.title,
         "updated_by": user.username or user.id},
        correlation_id=request.state.correlation_id,
    )
    log.info("events.announcement.updated", announcement_id=out.id, by=user.id)
    return out


@router.delete("/announcements/{ann_id}",
               status_code=status.HTTP_204_NO_CONTENT,
               summary="Delete an announcement (admin / instructor)")
async def delete_announcement(ann_id: str,
                              request: Request,
                              user: UserContext = Depends(require_role(*EDITOR_ROLES))):
    async with get_sessionmaker()() as s:
        a = await s.get(Announcement, ann_id)
        if a is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Announcement not found")
        await s.delete(a)
        await s.commit()

    await request.app.state.publisher.publish(
        "announcement.deleted",
        {"service": "events-service",
         "timestamp": datetime.now(timezone.utc).isoformat(),
         "announcement_id": ann_id,
         "deleted_by": user.username or user.id},
        correlation_id=request.state.correlation_id,
    )
    log.info("events.announcement.deleted", announcement_id=ann_id, by=user.id)
    return None
