from __future__ import annotations

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


# Ingest (F11 direct write path)

class IngestRecord(BaseModel):
    kind:           str = Field("log", pattern="^(event|log|telemetry)$")
    routing_key:    str = ""
    service:        str = ""
    correlation_id: str = ""
    timestamp:      datetime | None = None
    message:        str = ""
    payload:        dict[str, Any] = Field(default_factory=dict)


class IngestResult(BaseModel):
    id: str


# Query / serve (F12)

class RecordOut(BaseModel):
    id:             str
    kind:           str = ""
    routing_key:    str = ""
    service:        str = ""
    correlation_id: str = ""
    timestamp:      str = ""
    ingested_at:    str = ""
    message:        str = ""
    payload:        dict[str, Any] = Field(default_factory=dict)
    seq:            int | None = None      # position in the hash chain
    hash:           str = ""
    signer:         str = ""               # publisher whose signature was verified


class RecordPage(BaseModel):
    total: int
    items: list[RecordOut]


class TermCount(BaseModel):
    key:   str
    count: int


class StatsOut(BaseModel):
    backend_reachable: bool
    total:             int
    by_service:        list[TermCount]
    by_routing_key:    list[TermCount]
    by_kind:           list[TermCount]


# Retention / hygiene

class DeleteRequest(BaseModel):
    all:    bool = False
    ids:    list[str] = Field(default_factory=list)
    before: datetime | None = None


class DeleteResult(BaseModel):
    deleted: int
