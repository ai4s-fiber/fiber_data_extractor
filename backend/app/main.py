"""FastAPI application entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import close_database
from app.core.health_checks import check_database, check_mineru_cloud_configured
from app.core.redis_client import close_redis, ping_redis
from app.core.schema_repair import ensure_runtime_schema
# Ensure all model tables are registered in Base.metadata for FK resolution
import app.models  # noqa: F401
from app.api import projects, papers, candidates, exports
from app.services.extraction_jobs import extraction_job_backend
from app.services.progress_bus import progress_bus

logger = logging.getLogger(__name__)


async def _queue_poller() -> None:
    while True:
        try:
            await extraction_job_backend.try_start_next()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Extraction queue polling failed")
        await asyncio.sleep(max(1, settings.EXTRACTION_JOB_POLL_INTERVAL_SECONDS))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ensure_runtime_schema()
    await extraction_job_backend.recover_interrupted_jobs()
    await extraction_job_backend.try_start_next()
    poller = asyncio.create_task(_queue_poller())
    try:
        yield
    finally:
        poller.cancel()
        with suppress(asyncio.CancelledError):
            await poller
        await extraction_job_backend.shutdown()
        await progress_bus.close()
        await close_redis()
        await close_database()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes — /api (legacy) and /api/v1 (versioned)
_API_ROUTERS = [
    (projects.router, "/api"),
    (papers.router, "/api"),
    (candidates.router, "/api"),
    (exports.router, "/api"),
]
for router, prefix in _API_ROUTERS:
    app.include_router(router, prefix=prefix)
for router, _ in _API_ROUTERS:
    app.include_router(router, prefix="/api/v1")


@app.get("/api/health")
@app.get("/api/v1/health")
async def health():
    redis_ok = await ping_redis() if settings.REDIS_ENABLED else None
    db_ok = await check_database()
    mineru_ok = check_mineru_cloud_configured()
    redis_required_ok = redis_ok is not False if settings.REDIS_ENABLED else True
    mineru_required_ok = mineru_ok if (
        settings.MINERU_ENABLED and settings.DEFAULT_PARSER_STRATEGY == "mineru_cloud"
    ) else True
    healthy = db_ok and redis_required_ok and mineru_required_ok
    return {
        "status": "ok" if healthy else "degraded",
        "version": settings.APP_VERSION,
        "database": db_ok,
        "redis": redis_ok,
        "mineru_cloud": mineru_ok,
        "progress_bus": "redis" if redis_ok else "memory",
        "features": {
            "list_cache": bool(redis_ok),
            "extraction_queue": bool(redis_ok),
        },
    }
