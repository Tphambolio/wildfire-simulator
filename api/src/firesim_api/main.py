"""FastAPI application factory.

Creates the FastAPI app with all routers, middleware, and shared services.

Usage:
    uvicorn firesim_api.main:app --reload
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from firesim_api.routers import fwi, health, simulations, weather
from firesim_api.services.runner import SimulationRunner
from firesim_api.ws.manager import ConnectionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# Shared services (module-level so they survive app recreation in tests)
_runner = SimulationRunner()
_ws_manager = ConnectionManager()


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    _ws_manager.set_loop(asyncio.get_event_loop())
    logging.getLogger(__name__).info("FireSim API started")
    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(
        title="FireSim API",
        description="Canadian FBP wildfire spread simulation API",
        version="3.0.0",
        lifespan=lifespan,
    )

    # CORS — allow frontend dev server
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Inject services into routers
    simulations.runner = _runner
    simulations.ws_manager = _ws_manager

    # Register routers
    application.include_router(health.router)
    application.include_router(simulations.router)
    application.include_router(weather.router)
    application.include_router(fwi.router)

    return application


app = create_app()
