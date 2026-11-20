from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import json
from typing import Any, AsyncIterator

from opensearchpy import AsyncOpenSearch

from cyber_range_core import get_logger


log = get_logger(__name__)

INDEX = "sor-records"

_MAPPING: dict[str, Any] = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        # Custom analyzer: split on any run of non-alphanumerics and lowercase.
        # This makes dotted routing keys ("iam.user.login") and key=value pairs
        # ("username=charon") tokenize into searchable words (iam, user, login,
        # username, charon) — the default standard analyzer keeps dotted tokens
        # whole, which breaks intuitive free-text search.
        "analysis": {
            "tokenizer": {
                "sor_split": {"type": "pattern", "pattern": r"[^A-Za-z0-9]+"},
            },
            "analyzer": {
                "sor_text": {
                    "type": "custom",
                    "tokenizer": "sor_split",
                    "filter": ["lowercase"],
                },
            },
        },
    },
    "mappings": {
        "properties": {
            "kind":           {"type": "keyword"},          # event | log | telemetry
            "routing_key":    {"type": "keyword"},
            "service":        {"type": "keyword"},
            "correlation_id": {"type": "keyword"},
            "timestamp":      {"type": "date"},             # when the thing happened
            "ingested_at":    {"type": "date"},             # when we stored it
            "message":        {"type": "text", "analyzer": "sor_text"},  # free-text searchable
            # The full original envelope/record. `enabled: false` stores it
            # verbatim without indexing every nested field (we index the bits
            # we filter on as top-level keyword fields above).
            "payload":        {"type": "object", "enabled": False},
            # Tamper evidence (SEC-012/SEC-004): every record carries its
            # position in a hash chain over the whole archive.
            "seq":            {"type": "long"},
            "prev_hash":      {"type": "keyword"},
            "hash":           {"type": "keyword"},
            "signer":         {"type": "keyword"},   # service whose signature was verified
        }
    },
}


GENESIS = "0" * 64
_CHAIN_FIELDS = ("kind", "routing_key", "service", "correlation_id", "timestamp",
                 "ingested_at", "message", "payload", "signer", "seq", "prev_hash")


def record_hash(doc: dict) -> str:
    """SHA-256 over the canonical JSON of the chained fields (incl. prev_hash)."""
    body = {k: doc.get(k) for k in _CHAIN_FIELDS}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, default=str).encode()).hexdigest()


class RecordStore:
    def __init__(self, hosts: str):
        # hosts: comma-separated http(s) URLs, e.g. "http://sor_opensearch:9200"
        self._hosts = [h.strip() for h in hosts.split(",") if h.strip()]
        self._client: AsyncOpenSearch | None = None
        self._chain_lock = asyncio.Lock()
        self._last_seq: int | None = None
        self._last_hash: str = GENESIS

    @property
    def client(self) -> AsyncOpenSearch:
        if self._client is None:
            self._client = AsyncOpenSearch(
                hosts=self._hosts,
                http_compress=True,
                use_ssl=False,
                verify_certs=False,
                ssl_show_warn=False,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def reachable(self) -> bool:
        try:
            return bool(await self.client.ping())
        except Exception:
            return False

    async def ensure_index(self) -> None:
        try:
            exists = await self.client.indices.exists(index=INDEX)
            if not exists:
                await self.client.indices.create(index=INDEX, body=_MAPPING)
                log.info("data.index.created", index=INDEX)
            else:
                props = {k: _MAPPING["mappings"]["properties"][k]
                         for k in ("seq", "prev_hash", "hash", "signer")}
                await self.client.indices.put_mapping(index=INDEX, body={"properties": props})
        except Exception as exc:
            log.warning("data.index.ensure_failed", error=str(exc))

    # Write (F11)

    async def _load_chain_head(self) -> None:
        res = await self.client.search(index=INDEX, body={
            "size": 1, "query": {"exists": {"field": "seq"}},
            "sort": [{"seq": {"order": "desc"}}]})
        hits = res.get("hits", {}).get("hits", [])
        if hits:
            self._last_seq = int(hits[0]["_source"]["seq"])
            self._last_hash = hits[0]["_source"]["hash"]
        else:
            self._last_seq, self._last_hash = 0, GENESIS

    async def init_chain(self) -> int:
        """Called at start-up: chain any records that pre-date the hash chain
        (oldest first), then load the head. Returns how many were chained."""
        async with self._chain_lock:
            await self.client.indices.refresh(index=INDEX)
            await self._load_chain_head()
            legacy: list[dict] = []
            body = {"size": 1000, "query": {"bool": {"must_not": {"exists": {"field": "seq"}}}},
                    "sort": [{"timestamp": {"order": "asc"}}, {"ingested_at": {"order": "asc"}}]}
            res = await self.client.search(index=INDEX, body=body, scroll="2m")
            sid = res.get("_scroll_id")
            try:
                while res.get("hits", {}).get("hits"):
                    legacy += res["hits"]["hits"]
                    res = await self.client.scroll(scroll_id=sid, scroll="2m")
                    sid = res.get("_scroll_id")
            finally:
                if sid:
                    try: await self.client.clear_scroll(scroll_id=sid)
                    except Exception: pass
            for h in legacy:
                src = h["_source"]
                src.setdefault("signer", "")
                src["seq"] = self._last_seq + 1
                src["prev_hash"] = self._last_hash
                src["hash"] = record_hash(src)
                await self.client.index(index=INDEX, id=h["_id"], body=src, refresh=False)
                self._last_seq, self._last_hash = src["seq"], src["hash"]
            if legacy:
                await self.client.indices.refresh(index=INDEX)
                log.info("data.chain.backfilled", records=len(legacy))
            log.info("data.chain.head", seq=self._last_seq)
            return len(legacy)

    # Write (F11)
    async def index_record(self, doc: dict) -> str | None:
        """Append one record to the hash chain. Returns its id, or None."""
        doc.setdefault("ingested_at", _dt.datetime.now(_dt.timezone.utc).isoformat())
        doc.setdefault("signer", "")
        async with self._chain_lock:
            if self._last_seq is None:
                try:
                    await self._load_chain_head()
                except Exception as exc:
                    log.warning("data.chain.head_failed", error=str(exc))
                    return None
            doc["seq"] = self._last_seq + 1
            doc["prev_hash"] = self._last_hash
            doc["hash"] = record_hash(doc)
            try:
                res = await self.client.index(index=INDEX, body=doc, refresh=False)
            except Exception as exc:
                log.warning("data.index.failed",
                            routing_key=doc.get("routing_key"), error=str(exc))
                return None
            self._last_seq, self._last_hash = doc["seq"], doc["hash"]
            return res.get("_id")

    async def chained(self, ids: list[str] | None = None, before: str | None = None,
                      all: bool = False) -> list[dict]:
        """seq/hash of the records a deletion is about to remove."""
        if all:
            q: dict = {"match_all": {}}
        else:
            filt = []
            if ids:
                filt.append({"ids": {"values": ids}})
            if before:
                filt.append({"range": {"timestamp": {"lt": before}}})
            if not filt:
                return []
            q = {"bool": {"filter": filt}}
        out = []
        async for rec in self._scan(q, sort=[{"seq": {"order": "asc"}}]):
            if rec.get("seq") is not None:
                out.append({"seq": rec["seq"], "hash": rec.get("hash", "")})
        return out

    async def _scan(self, query: dict, sort: list) -> AsyncIterator[dict]:
        res = await self.client.search(index=INDEX, body={"size": 1000, "query": query,
                                                          "sort": sort}, scroll="2m")
        sid = res.get("_scroll_id")
        try:
            while True:
                hits = res.get("hits", {}).get("hits", [])
                if not hits:
                    break
                for h in hits:
                    yield {"id": h["_id"], **h["_source"]}
                res = await self.client.scroll(scroll_id=sid, scroll="2m")
                sid = res.get("_scroll_id")
        finally:
            if sid:
                try: await self.client.clear_scroll(scroll_id=sid)
                except Exception: pass

    async def verify_chain(self) -> dict:
        """Recompute the whole chain. Gaps are accepted only when a deletion
        record documents the removed records' hashes."""
        await self.client.indices.refresh(index=INDEX)
        documented: dict[int, str] = {}
        async for rec in self._scan({"term": {"routing_key": "data.records.deleted"}},
                                    sort=[{"seq": {"order": "asc"}}]):
            for d in (rec.get("payload") or {}).get("deleted", []):
                documented[int(d["seq"])] = d.get("hash", "")
        checked = gaps = 0
        problems: list[dict] = []
        expect_seq, prev = 1, GENESIS
        async for rec in self._scan({"exists": {"field": "seq"}},
                                    sort=[{"seq": {"order": "asc"}}]):
            seq = int(rec["seq"])
            while expect_seq < seq:                     # missing records
                if expect_seq in documented:
                    prev = documented[expect_seq]; gaps += 1
                else:
                    problems.append({"seq": expect_seq, "problem": "missing, undocumented"})
                    prev = None
                expect_seq += 1
            if prev is not None and rec.get("prev_hash") != prev:
                problems.append({"seq": seq, "id": rec["id"], "problem": "prev_hash mismatch"})
            if record_hash(rec) != rec.get("hash"):
                problems.append({"seq": seq, "id": rec["id"], "problem": "content modified"})
            prev, expect_seq = rec.get("hash"), seq + 1
            checked += 1
        unchained = await self.client.count(index=INDEX, body={
            "query": {"bool": {"must_not": {"exists": {"field": "seq"}}}}})
        return {"intact": not problems and not unchained.get("count", 0),
                "records_checked": checked, "documented_deletions": gaps,
                "unchained_records": unchained.get("count", 0),
                "head_seq": expect_seq - 1, "problems": problems[:50]}

    # Read (F12)

    @staticmethod
    def _build_query(*, service: str | None, routing: str | None,
                     kind: str | None, q: str | None,
                     time_from: str | None, time_to: str | None) -> dict:
        must: list[dict] = []
        filt: list[dict] = []
        if service:
            filt.append({"term": {"service": service}})
        if kind:
            filt.append({"term": {"kind": kind}})
        if routing:
            # prefix match on routing_key (e.g. "iam." matches iam.user.login)
            filt.append({"prefix": {"routing_key": routing}})
        if time_from or time_to:
            rng: dict[str, str] = {}
            if time_from:
                rng["gte"] = time_from
            if time_to:
                rng["lte"] = time_to
            filt.append({"range": {"timestamp": rng}})
        if q:
            must.append({"multi_match": {
                "query": q,
                "fields": ["message", "routing_key", "service", "correlation_id"],
                "type": "best_fields",
            }})
        if not must and not filt:
            return {"match_all": {}}
        return {"bool": {"must": must or [{"match_all": {}}], "filter": filt}}

    async def search(self, *, service=None, routing=None, kind=None, q=None,
                     time_from=None, time_to=None,
                     limit=100, offset=0) -> tuple[int, list[dict]]:
        body = {
            "query": self._build_query(service=service, routing=routing, kind=kind,
                                       q=q, time_from=time_from, time_to=time_to),
            "sort": [{"timestamp": {"order": "desc"}}],
            "from": offset,
            "size": limit,
        }
        try:
            res = await self.client.search(index=INDEX, body=body)
        except Exception as exc:
            log.warning("data.search.failed", error=str(exc))
            return 0, []
        hits = res.get("hits", {})
        total = hits.get("total", {}).get("value", 0)
        items = [{"id": h["_id"], **h["_source"]} for h in hits.get("hits", [])]
        return total, items

    async def scroll_all(self, *, service=None, routing=None, kind=None, q=None,
                         time_from=None, time_to=None,
                         batch=1000, max_records=100_000) -> AsyncIterator[dict]:
        """Stream every matching record (for controlled export, F12). Uses the
        scroll API so large exports don't have to fit in one response."""
        body = {
            "query": self._build_query(service=service, routing=routing, kind=kind,
                                       q=q, time_from=time_from, time_to=time_to),
            "sort": [{"timestamp": {"order": "desc"}}],
            "size": batch,
        }
        emitted = 0
        try:
            res = await self.client.search(index=INDEX, body=body, scroll="2m")
        except Exception as exc:
            log.warning("data.scroll.failed", error=str(exc))
            return
        scroll_id = res.get("_scroll_id")
        try:
            while True:
                hits = res.get("hits", {}).get("hits", [])
                if not hits:
                    break
                for h in hits:
                    yield {"id": h["_id"], **h["_source"]}
                    emitted += 1
                    if emitted >= max_records:
                        return
                res = await self.client.scroll(scroll_id=scroll_id, scroll="2m")
                scroll_id = res.get("_scroll_id")
        finally:
            if scroll_id:
                try:
                    await self.client.clear_scroll(scroll_id=scroll_id)
                except Exception:
                    pass

    async def count(self, **filters) -> int:
        body = {"query": self._build_query(
            service=filters.get("service"), routing=filters.get("routing"),
            kind=filters.get("kind"), q=filters.get("q"),
            time_from=filters.get("time_from"), time_to=filters.get("time_to"))}
        try:
            res = await self.client.count(index=INDEX, body=body)
            return res.get("count", 0)
        except Exception:
            return 0

    async def aggregate_terms(self, field: str, size: int = 20) -> list[dict]:
        body = {"size": 0, "aggs": {"by": {"terms": {"field": field, "size": size}}}}
        try:
            res = await self.client.search(index=INDEX, body=body)
        except Exception:
            return []
        buckets = res.get("aggregations", {}).get("by", {}).get("buckets", [])
        return [{"key": b["key"], "count": b["doc_count"]} for b in buckets]

    # Retention / hygiene

    async def delete_one(self, doc_id: str) -> bool:
        try:
            await self.client.delete(index=INDEX, id=doc_id, refresh=True)
            return True
        except Exception:
            return False

    async def delete_by_query(self, *, all: bool = False, ids: list[str] | None = None,
                              before: str | None = None) -> int:
        if all:
            query: dict = {"match_all": {}}
        else:
            filt: list[dict] = []
            if ids:
                filt.append({"ids": {"values": ids}})
            if before:
                filt.append({"range": {"timestamp": {"lt": before}}})
            if not filt:
                return 0
            query = {"bool": {"filter": filt}}
        try:
            res = await self.client.delete_by_query(
                index=INDEX, body={"query": query}, refresh=True, conflicts="proceed")
            return res.get("deleted", 0)
        except Exception as exc:
            log.warning("data.delete.failed", error=str(exc))
            return 0
