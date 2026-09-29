import os

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse


# --------------------------------------------------
# Backend Service URLs
# --------------------------------------------------
#
# Phase 1 scope: the Gateway is a PURE reverse proxy. It does not add auth,
# does not reshape requests/responses, and does not know anything about the
# business meaning of a route - it only decides which backend service a
# path belongs to and forwards the request/response verbatim.

USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://localhost:8001")
TRAIN_SERVICE_URL = os.getenv("TRAIN_SERVICE_URL", "http://localhost:8002")
INVENTORY_SERVICE_URL = os.getenv("INVENTORY_SERVICE_URL", "http://localhost:8003")
BOOKING_SERVICE_URL = os.getenv("BOOKING_SERVICE_URL", "http://localhost:8004")
PAYMENT_SERVICE_URL = os.getenv("PAYMENT_SERVICE_URL", "http://localhost:8005")
NOTIFICATION_SERVICE_URL = os.getenv("NOTIFICATION_SERVICE_URL", "http://localhost:8006")


# --------------------------------------------------
# Route-prefix -> backend service mapping
# --------------------------------------------------
#
# Each incoming request looks like /api/<first-segment>/... . The first
# segment (after stripping the Gateway's own "/api" prefix) decides which
# service receives the request. The FULL original path (including that
# same first segment) is forwarded unchanged, because every backend service
# already owns that path today - e.g. /api/trains -> {TRAIN_SERVICE_URL}/trains.
#
# Inventory Service does not put all of its routes under one prefix
# (it has /inventory, /availability/{id}, /reserve/{id}, /release/{id}), so
# each of those top-level segments is mapped individually to the same
# service URL rather than relying on a single common prefix.
#
# "reserve" and "release" are deliberately NOT mapped. They mutate seat
# inventory with no authentication of any kind, so routing them here would
# let any browser consume or free berths directly, with no booking and no
# payment. Booking Service is unaffected: it calls Inventory directly over
# the Docker network (INVENTORY_URL=http://inventory-service:8000), never
# through this Gateway. Inventory's own per-seat mutations are likewise kept
# off the Gateway by living under /internal/..., which is not mapped either -
# the same pattern as Booking Service's /internal/payment-events.

PREFIX_TO_SERVICE = {
    "users": USER_SERVICE_URL,
    "trains": TRAIN_SERVICE_URL,
    "inventory": INVENTORY_SERVICE_URL,
    "availability": INVENTORY_SERVICE_URL,
    "bookings": BOOKING_SERVICE_URL,
    "payments": PAYMENT_SERVICE_URL,
    "notifications": NOTIFICATION_SERVICE_URL,
}

# Hop-by-hop / framing headers (RFC 7230 S6.1) that must never be forwarded
# verbatim between the two legs of a proxied request - letting httpx and
# Starlette each recompute these avoids Content-Length/Transfer-Encoding
# mismatches and stale Host headers reaching the upstream service.
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}

app = FastAPI(title="IRCTC API Gateway")

# One shared client for the app's lifetime, reused across requests.
client = httpx.AsyncClient(timeout=30.0)


@app.on_event("shutdown")
async def shutdown_event():
    await client.aclose()


@app.get("/")
def health():
    """Gateway's own liveness/diagnostic endpoint - never proxied."""
    return {
        "service": "IRCTC API Gateway",
        "status": "running",
        "routes": PREFIX_TO_SERVICE,
    }


@app.api_route(
    "/api/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def proxy(full_path: str, request: Request):
    """
    Pure reverse proxy: forwards /api/<anything> to whichever backend
    service owns the first path segment, preserving method, path, query
    string, headers, and body exactly as received. No auth, no request or
    response reshaping - that is out of scope for Phase 1.
    """
    first_segment = full_path.split("/", 1)[0]
    target_base = PREFIX_TO_SERVICE.get(first_segment)

    if target_base is None:
        return JSONResponse(
            status_code=404,
            content={
                "detail": f"No backend service registered for path '/{first_segment}'"
            },
        )

    target_url = f"{target_base}/{full_path}"

    body = await request.body()

    forward_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }

    try:
        upstream_response = await client.request(
            method=request.method,
            url=target_url,
            params=request.query_params.multi_items(),
            headers=forward_headers,
            content=body,
        )
    except httpx.RequestError as error:
        return JSONResponse(
            status_code=502,
            content={"detail": f"Upstream service unreachable: {error}"},
        )

    response_headers = {
        key: value
        for key, value in upstream_response.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )
