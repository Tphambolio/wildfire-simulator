"""Health check endpoint."""

from __future__ import annotations

import os
import time

from fastapi import APIRouter

router = APIRouter(tags=["health"])

_start_time = time.time()


@router.get("/api/v1/health")
async def health_check() -> dict:
    """Health check with uptime and version."""
    return {
        "status": "healthy",
        "version": "3.0.0",
        "uptime_seconds": round(time.time() - _start_time, 1),
        "engine": "firesim",
    }


@router.get("/api/v1/version")
async def version() -> dict:
    """API version and the git commit the deployed build came from (stamped on briefings)."""
    return {
        "version": "3.0.0",
        "git_sha": os.environ.get("FIRESIM_GIT_SHA") or "unknown",
    }
