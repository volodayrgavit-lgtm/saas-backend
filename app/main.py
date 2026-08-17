import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.core.exceptions import AppError

# Import routers
from app.modules.auth.router import router as auth_router
from app.modules.users.router import router as users_router
from app.modules.catalog.router import router as catalog_router
from app.modules.referrals.router import router as referrals_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup
    logger.info(f"Starting {settings.APP_NAME} on {settings.HOST}:{settings.PORT}")

    # Start outbox worker as background task
    from app.modules.events.worker import outbox_worker_loop
    worker_task = asyncio.create_task(outbox_worker_loop())

    yield

    # Shutdown
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    logger.info(f"Shutting down {settings.APP_NAME}")


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Global error handler ──
@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, **exc.details}},
    )


# ── Routers ──
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(catalog_router)
app.include_router(referrals_router)


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": settings.APP_NAME}