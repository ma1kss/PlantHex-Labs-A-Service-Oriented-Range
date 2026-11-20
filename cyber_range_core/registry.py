from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)


@dataclass
class ServiceEndpoint:
    """One resolved service instance."""
    service_id: str
    service_name: str
    address: str
    port: int
    tags: list[str] = field(default_factory=list)

    @property
    def base_url(self) -> str:
        return f"http://{self.address}:{self.port}"


class RegistryClient:
    """
    Async Consul client. Methods degrade gracefully if Consul is unreachable
    so services can still start on a local VM without the registry running.
    """

    def __init__(
        self,
        host: str,
        port: int = 8500,
        scheme: str = "http",
        timeout: float = 5.0,
    ):
        self.host   = host
        self.port   = port
        self.scheme = scheme
        self.base   = f"{scheme}://{host}:{port}/v1"
        self._client = httpx.AsyncClient(timeout=timeout)
        self.enabled = bool(host)

    async def close(self):
        await self._client.aclose()

    # Registration

    async def register(
        self,
        service_id: str,
        service_name: str,
        address: str,
        port: int,
        tags: list[str] | None = None,
        health_check_url: str | None = None,
        health_interval: str = "15s",
        health_timeout: str = "5s",
        deregister_critical_after: str = "1m",
        meta: dict[str, str] | None = None,
        health_tls_skip_verify: bool = False,
    ) -> bool:
        if not self.enabled:
            log.warning("Consul disabled — skipping registration of %s", service_id)
            return False

        payload: dict[str, Any] = {
            "ID":      service_id,
            "Name":    service_name,
            "Address": address,
            "Port":    port,
            "Tags":    tags or [],
        }
        if meta:
            payload["Meta"] = meta
        if health_check_url:
            payload["Check"] = {
                "HTTP":     health_check_url,
                "Interval": health_interval,
                "Timeout":  health_timeout,
                "DeregisterCriticalServiceAfter": deregister_critical_after,
            }
            if health_tls_skip_verify:
                payload["Check"]["TLSSkipVerify"] = True

        try:
            r = await self._client.put(f"{self.base}/agent/service/register", json=payload)
            r.raise_for_status()
            log.info("Registered service %s (%s) in Consul", service_id, service_name)
            return True
        except Exception as exc:
            log.error("Consul registration failed for %s: %s", service_id, exc)
            return False

    async def deregister_stale(self, service_name: str, keep_id: str) -> int:
        """
        Remove leftover registrations of `service_name` whose ID differs from
        `keep_id`. Service IDs embed the container hostname, so every rebuild
        registers under a new ID; the old entry keeps passing its health check
        (the address is the compose DNS alias, which now resolves to the new
        container) and would otherwise linger forever — Prometheus then scrapes
        the same service twice and every dashboard double-counts.
        """
        if not self.enabled:
            return 0
        try:
            r = await self._client.get(f"{self.base}/agent/services")
            r.raise_for_status()
            services: dict[str, Any] = r.json()
        except Exception as exc:
            log.warning("Consul stale-sweep list failed: %s", exc)
            return 0

        removed = 0
        for sid, svc in services.items():
            if svc.get("Service") == service_name and sid != keep_id:
                if await self.deregister(sid):
                    log.info("Removed stale registration %s (%s)", sid, service_name)
                    removed += 1
        return removed

    async def deregister(self, service_id: str) -> bool:
        if not self.enabled:
            return False
        try:
            r = await self._client.put(f"{self.base}/agent/service/deregister/{service_id}")
            r.raise_for_status()
            log.info("Deregistered service %s from Consul", service_id)
            return True
        except Exception as exc:
            log.error("Consul deregistration failed for %s: %s", service_id, exc)
            return False

    # Discovery

    async def discover(self, service_name: str, passing_only: bool = True) -> list[ServiceEndpoint]:
        """
        Return all healthy instances of `service_name`.
        Returns an empty list if Consul unreachable or no instances registered.
        """
        if not self.enabled:
            return []
        try:
            r = await self._client.get(
                f"{self.base}/health/service/{service_name}",
                params={"passing": "true"} if passing_only else None,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            log.warning("Consul discover(%s) failed: %s", service_name, exc)
            return []

        endpoints: list[ServiceEndpoint] = []
        for entry in data:
            svc = entry.get("Service", {})
            endpoints.append(ServiceEndpoint(
                service_id=svc.get("ID", ""),
                service_name=svc.get("Service", ""),
                address=svc.get("Address", "") or entry.get("Node", {}).get("Address", ""),
                port=int(svc.get("Port", 0)),
                tags=svc.get("Tags", []) or [],
            ))
        return endpoints

    async def get_one(self, service_name: str) -> ServiceEndpoint | None:
        """
        Return one endpoint (first healthy). Caller can implement round-robin
        or any load-balancing strategy on top of discover().
        """
        endpoints = await self.discover(service_name)
        return endpoints[0] if endpoints else None

    async def list_services(self) -> dict[str, list[str]]:
        """{service_name: [tags...]} for everything currently registered."""
        if not self.enabled:
            return {}
        try:
            r = await self._client.get(f"{self.base}/catalog/services")
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            log.warning("Consul catalog list failed: %s", exc)
            return {}


# Convenience: make a stable service_id from hostname + port

def make_service_id(service_name: str, port: int) -> str:
    try:
        host = socket.gethostname()
    except Exception:
        host = "unknown"
    return f"{service_name}-{host}-{port}"
