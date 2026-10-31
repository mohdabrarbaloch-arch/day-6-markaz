"""Application entrypoint: middleware, routers, and the static SPA mount."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .database import init_db
from .ratelimit import SlidingWindowLimiter, client_key
from .routers import auth, deployments, health, pipelines, regions

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("markaz")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create tables on boot. Lifespan replaces the deprecated on_event hook."""
    init_db()
    logger.info("markaz starting env=%s", settings.environment)
    yield
    logger.info("markaz stopping")


app = FastAPI(
    title="Markaz API",
    version="1.0.0",
    description=(
        "An enterprise AI control plane. Declare what should run, where, and at what "
        "cost -- and find out from arithmetic whether it fits before you deploy."
    ),
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)

write_limiter = SlidingWindowLimiter(settings.rate_limit_write_per_minute)


@app.middleware("http")
async def security_and_trace(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    request.state.request_id = request_id

    # Rate-limit every mutating call, keyed on client. Auth routes have their own
    # tighter limiter inside the router. Exempt the docs and health probe so a
    # monitoring loop cannot lock itself out.
    if request.method in {"POST", "PATCH", "PUT", "DELETE"} and not request.url.path.startswith(
        ("/api/health",)
    ):
        try:
            write_limiter.check(client_key(request))
        except Exception as exc:  # HTTPException from the limiter
            if hasattr(exc, "status_code") and hasattr(exc, "detail"):
                return JSONResponse(
                    status_code=exc.status_code,  # type: ignore[attr-defined]
                    content={"detail": exc.detail, "request_id": request_id},  # type: ignore[attr-defined]
                    headers=getattr(exc, "headers", None),  # type: ignore[arg-type]
                )
            raise

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    return response


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(regions.router)
app.include_router(deployments.router)
app.include_router(pipelines.router)

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")


@app.exception_handler(StarletteHTTPException)
def _not_found(request: Request, exc: StarletteHTTPException):  # type: ignore[no-untyped-def]
    # API clients get JSON; browsers get the SPA shell so the router can handle it.
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "request_id": getattr(request.state, "request_id", ""),
            },
            headers=getattr(exc, "headers", None),
        )
    if exc.status_code == 404 and STATIC_DIR.exists():
        return FileResponse(STATIC_DIR / "index.html")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )
