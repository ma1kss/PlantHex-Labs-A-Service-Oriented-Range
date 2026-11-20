from __future__ import annotations

from typing import Callable, Awaitable

from fastapi import APIRouter, Response, status


def make_health_router(
    service_name: str,
    service_version: str,
    readiness_checks: dict[str, Callable[[], Awaitable[bool]]] | None = None,
) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health", summary="Liveness probe")
    async def health():
        return {
            "status":  "ok",
            "service": service_name,
            "version": service_version,
        }

    @router.get("/ready", summary="Readiness probe")
    async def ready(response: Response):
        results: dict[str, str] = {}
        all_ok = True
        if readiness_checks:
            for name, check in readiness_checks.items():
                try:
                    ok = await check()
                    results[name] = "ok" if ok else "fail"
                    if not ok:
                        all_ok = False
                except Exception as exc:
                    results[name] = f"fail: {exc}"
                    all_ok = False
        if not all_ok:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"ready": all_ok, "checks": results}

    return router
