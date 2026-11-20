from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class LaunchRequest(BaseModel):
    challenge_id: str = Field(..., min_length=1, max_length=36)


class SessionRef(BaseModel):
    """A reference to the instance Scenario deployed — Orchestration passes it
    back to the caller but does not own it."""
    id:           str
    challenge_id: str
    user_id:      str
    username:     str = ""
    status:       str = ""
    hostname:     str = "localhost"
    ports:        dict[str, str] = {}
    access_urls:  list[str] = []
    kind:         str = "single"   # single | lab
    subnet:       str = ""         # lab subnet (10.10.X.0/24)


class LaunchResult(BaseModel):
    launch_id:    str
    challenge_id: str
    session:      SessionRef | None = None
    steps:        list[str]          # coordination steps that completed
    warnings:     list[str] = []


class TeardownResult(BaseModel):
    launch_id:  str | None = None
    session_id: str
    status:     str
    steps:      list[str]
    warnings:   list[str] = []


class LaunchOut(BaseModel):
    id:            str
    challenge_id:  str
    user_id:       str
    username:      str
    session_id:    str
    status:        str
    warnings:      list[str] = []
    created_at:    datetime
    torn_down_at:  datetime | None = None
