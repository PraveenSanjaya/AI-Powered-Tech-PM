import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import actions, assistant, auth, data, integrations, workspaces
from app.core.config import get_settings
from app.core.db import get_engine
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
log = logging.getLogger("techpm")

app = FastAPI(title="AI-Powered Tech PM API", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True,
                   allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   allow_headers=["content-type", "x-csrf-token"])


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id", uuid.uuid4().hex[:12])
    try:
        response = await call_next(request)
    except Exception:  # noqa: BLE001
        log.exception("unhandled error", extra={"request_id": rid, "path": request.url.path})
        return JSONResponse({"detail": "Internal server error", "request_id": rid}, status_code=500)
    response.headers["x-request-id"] = rid
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["cache-control"] = "no-store"
    if not request.url.path.endswith(("/healthz", "/readyz")):
        log.info("request", extra={"request_id": rid, "path": request.url.path, "status": response.status_code})
    return response


for r in (auth.router, workspaces.router, data.router, assistant.router, actions.router, integrations.router):
    app.include_router(r, prefix="/api")


@app.get("/healthz")
@app.get("/api/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
@app.get("/api/readyz")
def readyz():
    try:
        with get_engine().connect() as c:
            c.execute(text("select 1"))
    except Exception:  # noqa: BLE001
        return JSONResponse({"status": "unavailable", "database": False}, status_code=503)
    return {"status": "ready", "database": True, "demo_mode": settings.demo_mode, "llm_provider": settings.llm_provider}
