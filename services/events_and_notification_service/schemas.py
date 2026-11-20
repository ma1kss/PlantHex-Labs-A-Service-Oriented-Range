from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


# Announcements

class AnnouncementIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    body:  str = Field("", max_length=10_000)


class AnnouncementOut(BaseModel):
    id:          str
    title:       str
    body:        str
    author_id:   str
    author_name: str
    author_role: str
    created_at:  datetime
    updated_at:  datetime
