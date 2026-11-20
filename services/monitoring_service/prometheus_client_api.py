from __future__ import annotations

import httpx


class PrometheusClient:
    def __init__(self, base_url: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def reachable(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.get(f"{self.base_url}/-/ready")
                return r.status_code == 200
        except Exception:
            return False

    async def instant_query(self, promql: str) -> list[dict]:
        """Return the raw `result` list from an instant query, or [] on error."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.get(f"{self.base_url}/api/v1/query", params={"query": promql})
                r.raise_for_status()
                body = r.json()
            if body.get("status") != "success":
                return []
            return body.get("data", {}).get("result", []) or []
        except Exception:
            return []

    async def targets(self) -> list[dict]:
        """Return active scrape targets (normalised)."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.get(f"{self.base_url}/api/v1/targets", params={"state": "active"})
                r.raise_for_status()
                body = r.json()
        except Exception:
            return []

        out: list[dict] = []
        for t in body.get("data", {}).get("activeTargets", []) or []:
            labels = t.get("labels", {}) or {}
            out.append({
                "service":      labels.get("service") or labels.get("job") or "",
                "instance":     labels.get("instance") or "",
                "health":       t.get("health", "unknown"),     # up | down | unknown
                "last_scrape":  t.get("lastScrape", ""),
                "last_error":   t.get("lastError", ""),
                "scrape_url":   t.get("scrapeUrl", ""),
            })
        return out
