"""FastAPI application factory.

Creates the FastAPI app with all routers, middleware, and shared services.

Usage:
    uvicorn firesim_api.main:app --reload
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from firesim_api.routers import fwi, health, layers, simulations, weather
from firesim_api.services import archive as run_archive
from firesim_api.services.runner import SimulationRunner
from firesim_api.ws.manager import ConnectionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# Shared services (module-level so they survive app recreation in tests)
_runner = SimulationRunner()
_ws_manager = ConnectionManager()


async def _retention_loop() -> None:
    """Run archive retention at startup and every FIRESIM_ARCHIVE_RETENTION_HOURS (24 h)."""
    log = logging.getLogger(__name__)
    while True:
        archive = run_archive.get_archive()
        if archive.enabled:
            try:
                await asyncio.get_event_loop().run_in_executor(None, archive.run_retention)
            except Exception:  # noqa: BLE001 - retried next pass
                log.exception("Run archive retention failed")
        await asyncio.sleep(max(0.01, archive.config.retention_hours) * 3600.0)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    _ws_manager.set_loop(asyncio.get_event_loop())
    run_archive.get_archive()  # configure now, so its warnings appear at startup
    retention = asyncio.create_task(_retention_loop())
    logging.getLogger(__name__).info("FireSim API started")
    try:
        yield
    finally:
        retention.cancel()
        run_archive.get_archive().shutdown()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(
        title="FireSim API",
        description="Canadian FBP wildfire spread simulation API",
        version="3.0.0",
        lifespan=lifespan,
    )

    # CORS — defaults to "*" for local dev; set FIRESIM_CORS_ORIGINS in production
    _cors_origins = os.environ.get("FIRESIM_CORS_ORIGINS", "*").split(",")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Inject services into routers
    simulations.runner = _runner
    simulations.ws_manager = _ws_manager
    # Finished runs go to the run archive (services/archive.py) in the background
    _runner.on_finished = simulations.archive_finished_run

    # Register routers
    application.include_router(health.router)
    application.include_router(simulations.router)
    application.include_router(weather.router)
    application.include_router(fwi.router)
    application.include_router(layers.router)

    return application


app = create_app()
