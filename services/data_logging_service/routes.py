from __future__ import annotations

import csv
import io
import json
from xml.sax.saxutils import escape as _xml_escape
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse

from cyber_range_core import UserContext, get_logger

import schemas
from security import require_auth_local, require_role


STAFF_ROLES = ("admin", "instructor")

log    = get_logger(__name__)
router = APIRouter()


def _store(request: Request):
    return request.app.state.store


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# F11: synchronous ingest

@router.post("/ingest",
             response_model=schemas.IngestResult,
             status_code=status.HTTP_201_CREATED,
             summary="Submit a single record directly (F11)")
async def ingest(body: schemas.IngestRecord,
                 request: Request,
                 user: UserContext = Depends(require_role("admin"))):
    now = datetime.now(timezone.utc).isoformat()
    payload = dict(body.payload or {})
    if body.timestamp is not None:
        payload["client_timestamp"] = body.timestamp.isoformat()   # kept, not trusted
    if body.service:
        payload["claimed_service"] = body.service
    doc = {
        "kind":           body.kind,
        "routing_key":    body.routing_key,
        "service":        f"direct:{user.username or user.id}",
        "correlation_id": body.correlation_id,
        "timestamp":      now,
        "message":        body.message or body.routing_key,
        "payload":        payload,
    }
    doc_id = await _store(request).index_record(doc)
    log.info("data.ingest", kind=body.kind, by=user.id, doc_id=doc_id)
    return schemas.IngestResult(id=doc_id or "")


async def _audit(request: Request, routing_key: str, user: UserContext, payload: dict) -> None:
    """Write an audit record straight into the chained archive."""
    await _store(request).index_record({
        "kind": "event", "routing_key": routing_key,
        "service": "data-logging-service",
        "correlation_id": getattr(request.state, "correlation_id", ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": f"{routing_key} by={user.username or user.id}",
        "payload": {"user_id": user.id, "username": user.username,
                    "roles": user.roles, **payload},
    })


@router.get("/integrity",
            summary="Verify the archive's hash chain (tamper evidence, SEC-012)")
async def integrity(request: Request,
                    _: UserContext = Depends(require_role(*STAFF_ROLES))):
    return await _store(request).verify_chain()


# F12: query / serve

@router.get("/logs",
            response_model=schemas.RecordPage,
            summary="Unified query over stored records (F12)")
async def query_logs(
    request: Request,
    _: UserContext = Depends(require_role(*STAFF_ROLES)),
    service: Annotated[str | None, Query()] = None,
    routing: Annotated[str | None, Query()] = None,
    kind:    Annotated[str | None, Query()] = None,
    q:       Annotated[str | None, Query()] = None,
    time_from: Annotated[datetime | None, Query(alias="from")] = None,
    time_to:   Annotated[datetime | None, Query(alias="to")]   = None,
    limit:   Annotated[int, Query(ge=1, le=1000)] = 100,
    offset:  Annotated[int, Query(ge=0)]          = 0,
):
    total, items = await _store(request).search(
        service=service, routing=routing, kind=kind, q=q,
        time_from=_iso(time_from), time_to=_iso(time_to),
        limit=limit, offset=offset,
    )
    return schemas.RecordPage(
        total=total,
        items=[schemas.RecordOut(**_normalise(i)) for i in items],
    )


@router.get("/stats",
            response_model=schemas.StatsOut,
            summary="Aggregate counts over the store (F12)")
async def stats(request: Request,
                _: UserContext = Depends(require_role(*STAFF_ROLES))):
    store = _store(request)
    reachable = await store.reachable()
    if not reachable:
        return schemas.StatsOut(backend_reachable=False, total=0,
                                by_service=[], by_routing_key=[], by_kind=[])
    total = await store.count()
    by_service = await store.aggregate_terms("service")
    by_routing = await store.aggregate_terms("routing_key")
    by_kind    = await store.aggregate_terms("kind")
    return schemas.StatsOut(
        backend_reachable=True,
        total=total,
        by_service=[schemas.TermCount(**t) for t in by_service],
        by_routing_key=[schemas.TermCount(**t) for t in by_routing],
        by_kind=[schemas.TermCount(**t) for t in by_kind],
    )


# F12: controlled export

@router.get("/logs/export",
            summary="Export matching records in an interoperable form (F12, admin)")
async def export_logs(
    request: Request,
    user: UserContext = Depends(require_role("admin")),
    fmt:     Annotated[str, Query(pattern="^(json|ndjson|csv|xml)$")] = "ndjson",
    service: Annotated[str | None, Query()] = None,
    routing: Annotated[str | None, Query()] = None,
    kind:    Annotated[str | None, Query()] = None,
    q:       Annotated[str | None, Query()] = None,
    time_from: Annotated[datetime | None, Query(alias="from")] = None,
    time_to:   Annotated[datetime | None, Query(alias="to")]   = None,
):
    store = _store(request)
    kwargs = dict(service=service, routing=routing, kind=kind, q=q,
                  time_from=_iso(time_from), time_to=_iso(time_to))
    # Controlled export (SEC-017): who exported what is recorded in the
    # tamper-evident archive before any data leaves.
    await _audit(request, "data.export.performed", user,
                 {"format": fmt, "filters": {k: v for k, v in kwargs.items() if v},
                  "count": await store.count(**kwargs)})

    if fmt == "xml":
        async def gen_xml():
            yield '<?xml version="1.0" encoding="UTF-8"?>\n<records>\n'
            async for rec in store.scroll_all(**kwargs):
                n = _normalise(rec)
                fields = "".join(
                    f"<{k}>{_xml_escape(json.dumps(n[k]) if k == 'payload' else str(n[k]))}</{k}>"
                    for k in ("id", "kind", "routing_key", "service", "correlation_id",
                              "timestamp", "ingested_at", "message", "payload"))
                yield f"  <record>{fields}</record>\n"
            yield "</records>\n"
        return StreamingResponse(gen_xml(), media_type="application/xml",
                                 headers={"Content-Disposition": "attachment; filename=sor-records.xml"})

    if fmt == "ndjson":
        async def gen_ndjson():
            async for rec in store.scroll_all(**kwargs):
                yield json.dumps(_normalise(rec)) + "\n"
        return StreamingResponse(gen_ndjson(), media_type="application/x-ndjson",
                                 headers={"Content-Disposition": "attachment; filename=sor-records.ndjson"})

    if fmt == "json":
        async def gen_json():
            yield "["
            first = True
            async for rec in store.scroll_all(**kwargs):
                yield ("" if first else ",") + json.dumps(_normalise(rec))
                first = False
            yield "]"
        return StreamingResponse(gen_json(), media_type="application/json",
                                 headers={"Content-Disposition": "attachment; filename=sor-records.json"})

    # csv — flatten the top-level fields; payload is JSON-encoded in one column.
    async def gen_csv():
        buf = io.StringIO()
        writer = csv.writer(buf)
        cols = ["id", "kind", "routing_key", "service", "correlation_id",
                "timestamp", "ingested_at", "message", "payload"]
        writer.writerow(cols)
        yield buf.getvalue(); buf.seek(0); buf.truncate(0)
        async for rec in store.scroll_all(**kwargs):
            n = _normalise(rec)
            writer.writerow([
                n["id"], n["kind"], n["routing_key"], n["service"],
                n["correlation_id"], n["timestamp"], n["ingested_at"],
                n["message"], json.dumps(n["payload"]),
            ])
            yield buf.getvalue(); buf.seek(0); buf.truncate(0)
    return StreamingResponse(gen_csv(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=sor-records.csv"})


# Retention / hygiene

@router.delete("/logs",
               response_model=schemas.DeleteResult,
               summary="Bulk delete records (admin)")
async def delete_logs(body: schemas.DeleteRequest,
                      request: Request,
                      user: UserContext = Depends(require_role("admin"))):
    if not body.all and not body.ids and not body.before:
        from fastapi import HTTPException
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Specify all:true, ids:[...] or before:<iso>.")
    removed = await _store(request).chained(ids=body.ids, before=_iso(body.before), all=body.all)
    deleted = await _store(request).delete_by_query(
        all=body.all, ids=body.ids, before=_iso(body.before))
    await _audit(request, "data.records.deleted", user,
                 {"deleted": removed, "count": deleted, "all": body.all})
    log.info("data.delete", deleted=deleted, by=user.id, all=body.all)
    return schemas.DeleteResult(deleted=deleted)


@router.delete("/logs/{record_id}",
               status_code=status.HTTP_204_NO_CONTENT,
               summary="Delete one record (admin)")
async def delete_one(record_id: str,
                     request: Request,
                     user: UserContext = Depends(require_role("admin"))):
    removed = await _store(request).chained(ids=[record_id])
    if await _store(request).delete_one(record_id):
        await _audit(request, "data.records.deleted", user,
                     {"deleted": removed, "count": len(removed)})
    log.info("data.delete_one", record_id=record_id, by=user.id)
    return None


# helpers

def _normalise(rec: dict) -> dict:
    """Coerce a raw store hit into the RecordOut shape (fill missing fields)."""
    return {
        "id":             rec.get("id", ""),
        "kind":           rec.get("kind", ""),
        "routing_key":    rec.get("routing_key", ""),
        "service":        rec.get("service", ""),
        "correlation_id": rec.get("correlation_id", ""),
        "timestamp":      rec.get("timestamp", ""),
        "ingested_at":    rec.get("ingested_at", ""),
        "message":        rec.get("message", ""),
        "payload":        rec.get("payload", {}) or {},
        "seq":            rec.get("seq"),
        "hash":           rec.get("hash", ""),
        "signer":         rec.get("signer", ""),
    }
