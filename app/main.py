from contextlib import asynccontextmanager
import logging
import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import settings
from .database import Base, engine
from .db_session_context import (
    build_request_db_context,
    reset_request_db_context,
    set_request_db_context,
)
from .routers import (
    alerts,
    auth,
    availability,
    checkin,
    dashboard,
    employees,
    messages,
    schedule,
    settings as settings_router,
    shift_pool,
    shifts,
    swaps,
    tasks,
    websocket,
)
from .security import RateLimitMiddleware, RequestSizeLimitMiddleware, SecurityHeadersMiddleware
from .services.cache_service import close_cache_client
from .services.scheduler_service import start_scheduler
from .services.websocket_manager import manager


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ = manager
    Base.metadata.create_all(bind=engine)

    scheduler = start_scheduler()
    app.state.scheduler = scheduler

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        close_cache_client()


app = FastAPI(title="Restaurant Task Manager", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.trusted_hosts_list() or ["*"],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list(),
    allow_origin_regex=settings.CORS_ALLOW_ORIGIN_REGEX,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    RequestSizeLimitMiddleware,
    max_body_bytes=settings.MAX_REQUEST_BODY_BYTES,
)

app.add_middleware(
    SecurityHeadersMiddleware,
    enabled=settings.SECURITY_HEADERS_ENABLED,
)

app.add_middleware(
    RateLimitMiddleware,
    enabled=settings.RATE_LIMIT_ENABLED,
    default_rate=settings.RATE_LIMIT_DEFAULT,
    login_rate=settings.RATE_LIMIT_LOGIN,
    checkin_rate=settings.RATE_LIMIT_CHECKIN,
    trust_proxy_headers=settings.TRUST_PROXY_HEADERS,
)


@app.middleware("http")
async def db_request_context_middleware(request: Request, call_next):
    token = set_request_db_context(build_request_db_context(request))
    try:
        return await call_next(request)
    finally:
        reset_request_db_context(token)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    started = time.perf_counter()
    response = None

    try:
        response = await call_next(request)
        return response
    finally:
        duration_ms = (time.perf_counter() - started) * 1000
        status_code = response.status_code if response is not None else 500
        logger.info(
            "%s %s -> %s (%.2fms)",
            request.method,
            request.url.path,
            status_code,
            duration_ms,
        )


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    del request

    errors = [
        {
            "loc": list(error.get("loc", [])),
            "msg": error.get("msg", "Invalid value"),
            "type": error.get("type", "validation_error"),
        }
        for error in exc.errors()
    ]

    return JSONResponse(
        status_code=422,
        content={
            "detail": "Validation failed",
            "errors": errors,
        },
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    del request

    if exc.status_code == 404 and str(exc.detail).lower() == "not found":
        return JSONResponse(status_code=404, content={"detail": "Not found"})

    if isinstance(exc.detail, str):
        detail = exc.detail
    else:
        detail = "Request failed"

    return JSONResponse(status_code=exc.status_code, content={"detail": detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled server error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(employees.router, prefix="/employees", tags=["employees"])
app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
app.include_router(shifts.router, prefix="/shifts", tags=["shifts"])
app.include_router(schedule.router, prefix="/schedule", tags=["schedule"])
app.include_router(checkin.router, prefix="", tags=["checkin"])
app.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
app.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
app.include_router(messages.router, prefix="/messages", tags=["messages"])
app.include_router(swaps.router, prefix="/swaps", tags=["swaps"])
app.include_router(availability.router, prefix="/availability", tags=["availability"])
app.include_router(shift_pool.router, prefix="/shift-pool", tags=["shift-pool"])
app.include_router(websocket.router, prefix="/ws", tags=["websocket"])
app.include_router(settings_router.router, prefix="/settings", tags=["settings"])


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
