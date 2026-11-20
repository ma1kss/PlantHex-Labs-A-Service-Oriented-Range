from __future__ import annotations

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse, JSONResponse

from cyber_range_core import get_logger

from security import (
    requires_edge_auth, decode_token, bearer_from_header, is_internal_path,
)


log = get_logger(__name__)
router = APIRouter()


# URL-path prefix → Consul service name. The prefix is stripped before the
# request is forwarded (so /iam/auth/login → iam-service /auth/login).
PREFIX_TO_SERVICE = {
    "iam":        "iam-service",
    "scenario":   "scenario-service",
    "events":     "events-service",
    "monitoring": "monitoring-service",
    "data":          "data-logging-service",
    "learning":      "learning-service",
    "orchestration": "orchestration-service",
}

# Hop-by-hop headers that must not be forwarded (RFC 7230 §6.1) plus Host,
# which httpx sets from the target URL.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


async def _resolve_base(request: Request, service_name: str) -> str | None:
    """Resolve a downstream base URL via the registry, falling back to the
    static map configured on settings."""
    registry = request.app.state.registry
    try:
        endpoint = await registry.get_one(service_name)
        if endpoint is not None:
            return endpoint.base_url
    except Exception as exc:
        log.warning("portal.resolve.registry_failed",
                    service=service_name, error=str(exc))
    return request.app.state.settings.downstream_map().get(service_name)


def _filtered_request_headers(request: Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items()
            if k.lower() not in _HOP_BY_HOP}


def _filtered_response_headers(resp: httpx.Response) -> dict[str, str]:
    return {k: v for k, v in resp.headers.items()
            if k.lower() not in _HOP_BY_HOP}


def _edge_auth_response(request: Request, full_path: str) -> JSONResponse | None:
    """Return a 401/404 response if the request may not pass the edge, else None.
    Checks: control-plane-only paths are never proxied; protected paths need a
    valid token whose session is neither revoked nor idle (SEC-022)."""
    if is_internal_path(full_path):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    if not requires_edge_auth(request.method, full_path):
        return None
    s = request.app.state.settings
    token = bearer_from_header(request.headers.get("authorization"))
    claims = decode_token(token, secret=s.JWT_SECRET,
                          algorithm=s.JWT_ALGORITHM, issuer=s.JWT_ISSUER)
    if claims is None:
        return JSONResponse({"detail": "Authentication required at the platform edge."},
                            status_code=401)
    reason = request.app.state.sessions.check(claims)
    if reason is not None:
        return JSONResponse({"detail": f"Authentication required: {reason}."},
                            status_code=401)
    return None


async def _proxy(request: Request, base_url: str, downstream_path: str) -> Response:
    """Stream the request to `base_url + downstream_path` and stream the
    response back."""
    target = base_url.rstrip("/") + downstream_path
    if request.url.query:
        target += "?" + request.url.query

    client: httpx.AsyncClient = request.app.state.http
    req = client.build_request(
        request.method,
        target,
        headers=_filtered_request_headers(request),
        content=request.stream(),       # stream the body (handles big uploads)
    )
    try:
        upstream = await client.send(req, stream=True)
    except httpx.RequestError as exc:
        log.warning("portal.proxy.upstream_error", target=target, error=str(exc))
        return JSONResponse(
            {"detail": f"Upstream service unavailable: {exc}"},
            status_code=502,
        )

    async def body_iter():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()

    return StreamingResponse(
        body_iter(),
        status_code=upstream.status_code,
        headers=_filtered_response_headers(upstream),
    )


# Consul catalog passthrough (dashboard tiles)

@router.get("/api/services")
async def services_catalog(request: Request) -> Response:
    s = request.app.state.settings
    base = f"{s.CONSUL_SCHEME}://{s.CONSUL_HOST}:{s.CONSUL_PORT}"
    client: httpx.AsyncClient = request.app.state.http
    try:
        r = await client.get(f"{base}/v1/catalog/services")
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as exc:
        return JSONResponse({"detail": str(exc)}, status_code=502)


# Per-service reverse proxy
# One catch-all route per known prefix. The trailing {path} is the downstream
# path; the prefix segment selects the service.

def _make_proxy_route(prefix: str, service_name: str):
    async def handler(path: str, request: Request) -> Response:
        full_path = f"/{prefix}/{path}"
        denied = _edge_auth_response(request, full_path)
        if denied is not None:
            return denied
        base = await _resolve_base(request, service_name)
        if base is None:
            return JSONResponse(
                {"detail": f"Service '{service_name}' is not registered."},
                status_code=503,
            )
        return await _proxy(request, base, "/" + path)
    return handler


for _prefix, _svc in PREFIX_TO_SERVICE.items():
    router.add_api_route(
        f"/{_prefix}/{{path:path}}",
        _make_proxy_route(_prefix, _svc),
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
