"""Authenticated, read-only Crypto Spot screening snapshot endpoint."""
from __future__ import annotations

from fastapi import Depends, FastAPI
from src.api.security import require_local_or_auth
from src.spot_screening import SpotScreener


def register_spot_screener_routes(app: FastAPI, service: SpotScreener) -> None:
    @app.get("/api/spot-screener", dependencies=[Depends(require_local_or_auth)])
    def spot_screener_status() -> dict:
        return service.status()
