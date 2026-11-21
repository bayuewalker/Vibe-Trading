"""Read-only Binance Spot market screener for autonomous research discovery.

This is NOT a TradingView data adapter. TradingView MCP terms do not allow
its data to drive automated trading decisions or an unattended data feed.
Signals are research rankings, never order intents.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from src.config.paths import get_runtime_root

logger = logging.getLogger(__name__)
BASE_URL = "https://api.binance.com"
VALID_SYMBOL = re.compile(r"^[A-Z0-9]{2,20}USDT$")
EXCLUDED_BASES = frozenset({"USDC", "FDUSD", "TUSD", "BUSD", "USDP", "DAI", "EUR", "TRY"})
EXCLUDED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")
SCAN_SECONDS = 15 * 60
MIN_QUOTE_VOLUME = 20_000_000
MAX_CANDIDATES = 12
MAX_RESULTS = 8


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def configured_interval() -> int:
    """Clamp operator-supplied interval to a safe provider quota range."""
    try:
        value = int(os.getenv("VIBE_SPOT_SCREEN_INTERVAL_SECONDS", str(SCAN_SECONDS)))
    except ValueError:
        return SCAN_SECONDS
    return min(86400, max(900, value))


def enabled() -> bool:
    return os.getenv("VIBE_SPOT_SCREEN_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def _positive(value: Any) -> float | None:
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _universe(tickers: Any, exchange_info: Any) -> list[str]:
    """Select liquid, actively tradable BINANCE spot/USDT pairs only."""
    if not isinstance(tickers, list) or not isinstance(exchange_info, dict):
        raise ValueError("Binance ticker/exchangeInfo payload invalid")
    symbols = exchange_info.get("symbols")
    if not isinstance(symbols, list):
        raise ValueError("Binance exchangeInfo missing symbols")
    eligible = {
        s.get("symbol") for s in symbols if isinstance(s, dict)
        and s.get("status") == "TRADING"
        and s.get("quoteAsset") == "USDT"
        and s.get("isSpotTradingAllowed") is True
    }
    qualified: list[tuple[str, float]] = []
    for ticker in tickers:
        if not isinstance(ticker, dict):
            continue
        symbol = ticker.get("symbol")
        if not isinstance(symbol, str) or not VALID_SYMBOL.fullmatch(symbol) or symbol not in eligible:
            continue
        base = symbol[:-4]
        if base in EXCLUDED_BASES or base.endswith(EXCLUDED_SUFFIXES):
            continue
        quote_volume = _positive(ticker.get("quoteVolume"))
        if quote_volume is None or quote_volume < MIN_QUOTE_VOLUME or _positive(ticker.get("lastPrice")) is None:
            continue
        qualified.append((symbol, quote_volume))
    qualified.sort(key=lambda item: (-item[1], item[0]))
    return [symbol for symbol, _ in qualified[:MAX_CANDIDATES]]


def _ema(values: list[float], period: int) -> float:
    acc = sum(values[:period]) / period
    weight = 2.0 / (period + 1)
    for value in values[period:]:
        acc += weight * (value - acc)
    return acc


def _rsi(values: list[float], period: int = 14) -> float:
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(v, 0.0) for v in changes[:period]]
    losses = [max(-v, 0.0) for v in changes[:period]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for change in changes[period:]:
        avg_gain = (avg_gain * (period - 1) + max(change, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0)) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def _features(rows: Any, now_ms: int) -> dict[str, float] | None:
    """Exclude the still-forming candle; don't claim live indicators are closed."""
    if not isinstance(rows, list):
        return None
    closed = [row for row in rows if isinstance(row, list) and len(row) >= 7
              and isinstance(row[6], (int, float)) and row[6] <= now_ms]
    if len(closed) < 55:
        return None
    closes: list[float] = []
    volumes: list[float] = []
    for row in closed:
        close = _positive(row[4])
        volume = _number(row[5])
        if close is None or volume is None or volume < 0:
            return None
        closes.append(close)
        volumes.append(volume)
    avg_volume = sum(volumes[-21:-1]) / 20
    if avg_volume <= 0:
        return None
    return {
        "price": closes[-1],
        "ema20": _ema(closes, 20),
        "ema50": _ema(closes, 50),
        "rsi14": _rsi(closes, 14),
        "relative_volume": volumes[-1] / avg_volume,
    }


def _rank(symbol: str, hourly: dict[str, float], four_hour: dict[str, float], quote_volume: float, change: float | None) -> dict[str, Any]:
    trend_1h = hourly["price"] > hourly["ema20"] > hourly["ema50"]
    trend_4h = four_hour["price"] > four_hour["ema20"] > four_hour["ema50"]
    rsi = hourly["rsi14"]
    relative_volume = hourly["relative_volume"]
    # Explicit, explainable score; not a probability, not a backtested edge.
    score = (30 if trend_4h else 0) + (25 if trend_1h else 0)
    score += (20 if 48 <= rsi <= 70 else 10 if 40 <= rsi <= 75 else 0)
    score += (15 if relative_volume >= 1.5 else 8 if relative_volume >= 1 else 0)
    score += (10 if change is not None and 0 < change <= 8 else 0)
    return {
        "symbol": symbol,
        "tv_symbol": f"BINANCE:{symbol}",
        "chart_url": f"https://www.tradingview.com/chart/?symbol=BINANCE%3A{symbol}",
        "score": score,
        "classification": "watchlist" if score >= 65 and trend_4h else "neutral",
        "price_usdt": round(hourly["price"], 8),
        "change_24h_pct": round(change, 2) if change is not None else None,
        "quote_volume_24h_usdt": round(quote_volume, 2),
        "rsi14_1h": round(rsi, 1),
        "relative_volume_1h": round(relative_volume, 2),
        "trend_1h": trend_1h,
        "trend_4h": trend_4h,
    }


@dataclass
class SpotScreener:
    """Single-process, fail-closed polling service with atomic volume snapshots."""
    path: Path | None = None

    def __post_init__(self) -> None:
        self.path = self.path or get_runtime_root() / "screening" / "crypto_spot.json"
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()
        self._last_error: str | None = None

    def status(self) -> dict[str, Any]:
        snapshot = None
        try:
            if self.path.exists():
                snapshot = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            logger.warning("Spot screener snapshot is unreadable", exc_info=True)
        return {
            "enabled": enabled(),
            "running": self._task is not None and not self._task.done(),
            "interval_seconds": configured_interval(),
            "source": "binance_spot_public_api",
            "trading_enabled": False,
            "last_error": self._last_error,
            "snapshot": snapshot,
        }

    def _save(self, payload: dict[str, Any]) -> None:
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".crypto-spot-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"), allow_nan=False)
            os.chmod(temp, 0o600)
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    async def scan_once(self, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
        async with self._lock:
            owns_client = client is None
            if client is None:
                client = httpx.AsyncClient(base_url=BASE_URL, timeout=12.0, follow_redirects=False)
            try:
                async def get(path: str, params: dict[str, Any] | None = None) -> Any:
                    assert client is not None
                    response = await client.get(path, params=params)
                    response.raise_for_status()
                    return response.json()

                tickers, info = await asyncio.gather(get("/api/v3/ticker/24hr"), get("/api/v3/exchangeInfo"))
                universe = _universe(tickers, info)
                if not universe:
                    raise ValueError("No liquid Binance USDT spot pairs returned")
                tickermap = {row["symbol"]: row for row in tickers if isinstance(row, dict) and "symbol" in row}
                sem = asyncio.Semaphore(3)
                now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

                async def evaluate(symbol: str) -> dict[str, Any] | None:
                    async with sem:
                        try:
                            one_h, four_h = await asyncio.gather(
                                get("/api/v3/klines", {"symbol": symbol, "interval": "1h", "limit": 90}),
                                get("/api/v3/klines", {"symbol": symbol, "interval": "4h", "limit": 90}),
                            )
                            hourly = _features(one_h, now_ms)
                            four_hour = _features(four_h, now_ms)
                            if hourly is None or four_hour is None:
                                return None
                            ticker = tickermap[symbol]
                            volume = _positive(ticker["quoteVolume"])
                            assert volume is not None
                            return _rank(symbol, hourly, four_hour, volume, _number(ticker.get("priceChangePercent")))
                        except (httpx.HTTPError, ValueError, TypeError, KeyError):
                            logger.warning("Spot screening skipped unavailable data for %s", symbol)
                            return None

                items = await asyncio.gather(*(evaluate(symbol) for symbol in universe))
                ranked = sorted((row for row in items if row is not None), key=lambda x: (-x["score"], -x["quote_volume_24h_usdt"], x["symbol"]))
                if not ranked:
                    raise ValueError("Binance returned no valid closed-candle candidates")
                payload = {
                    "as_of": _utc(), "source": "binance_spot_public_api",
                    "mode": "research_only", "timeframes": ["1h", "4h"],
                    "screened": len(universe), "qualified": sum(x["classification"] == "watchlist" for x in ranked),
                    "candidates": ranked[:MAX_RESULTS],
                }
                self._save(payload)
                self._last_error = None
                return payload
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                # Preserve last successful snapshot and mark operational failure separately.
                self._last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
                logger.warning("Spot screener scan failed: %s", type(exc).__name__)
                raise
            finally:
                if owns_client:
                    await client.aclose()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.scan_once()
            except Exception:
                logger.exception("Automatic spot scan failed; last good snapshot retained")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=configured_interval())
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        if not enabled() or self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="crypto-spot-screener")

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            await task
