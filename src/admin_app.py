#!/usr/bin/env python3
"""
FastAPI app for the Money House paper-trading admin dashboard.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .paper_service import PaperTradingService


LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
STATIC_DIR = Path(__file__).parent / "admin_static"

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
service = PaperTradingService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await service.start()
    await service.scan_once()
    try:
        await service.trigger_tests()
    except RuntimeError:
        pass
    yield
    await service.stop()


app = FastAPI(
    title="Money House Admin",
    version="1.0.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/healthz")
async def healthz() -> dict:
    snapshot = service.snapshot()
    return {
        "ok": True,
        "mode": snapshot["summary"]["mode"],
        "last_scan_at": snapshot["summary"]["last_scan_at"],
    }


@app.get("/api/dashboard")
async def dashboard() -> dict:
    return service.snapshot()


@app.post("/api/actions/scan")
async def scan_once() -> dict:
    return await service.scan_once()


@app.post("/api/actions/reset")
async def reset_bankroll() -> dict:
    return await service.reset_bankroll()


@app.post("/api/actions/tests")
async def run_tests() -> dict:
    try:
        return await service.trigger_tests()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.api_route("/", methods=["GET", "HEAD"])
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico")
async def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.svg")
