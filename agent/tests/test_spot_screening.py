"""Offline regression tests for automatic crypto spot screening."""
import json

import httpx
import pytest

from src import spot_screening as m


def candles(price_step=0.5):
    start = 1_700_000_000_000
    return [[start + i * 60_000, "1", "1", "1", str(10 + i * price_step),
             "100", start + i * 60_000 + 59_999] for i in range(90)]


def test_universe_filters_non_spot_leveraged_stable_and_illiquid():
    symbols = ["BTCUSDT", "ETHUSDT", "BTCUPUSDT", "USDCUSDT", "DOGEUSDT", "BTCUSDC"]
    tickers = [{"symbol": s, "quoteVolume": "30000000", "lastPrice": "1"} for s in symbols]
    tickers[-2]["quoteVolume"] = "100000"
    info = {"symbols": [{"symbol": s, "status": "TRADING", "quoteAsset": "USDT",
                         "isSpotTradingAllowed": True} for s in symbols]}
    assert m._universe(tickers, info) == ["BTCUSDT", "ETHUSDT"]


def test_excludes_open_candles_and_invalid_prices():
    rows = candles()
    rows[-1][6] = 2_000_000_000_000
    features = m._features(rows, 1_800_000_000_000)
    assert features is not None
    assert features["price"] == float(rows[-2][4])
    rows[-2][4] = "NaN"
    assert m._features(rows, 1_800_000_000_000) is None


def test_score_is_explainable_not_probability():
    h = {"price": 105.0, "ema20": 103.0, "ema50": 101.0, "rsi14": 60.0,
         "relative_volume": 1.6}
    row = m._rank("BTCUSDT", h, h, 40_000_000, 4.2)
    assert row["score"] == 100
    assert row["classification"] == "watchlist"
    assert row["tv_symbol"] == "BINANCE:BTCUSDT"
    h["ema50"] = 110.0
    assert m._rank("BTCUSDT", h, h, 40_000_000, 4.2)["classification"] == "neutral"


@pytest.mark.asyncio
async def test_scan_persists_ranked_closed_candle_rows(tmp_path):
    paths = []

    def responder(request):
        paths.append(request.url.path)
        if request.url.path.endswith("exchangeInfo"):
            return httpx.Response(200, json={"symbols": [
                {"symbol": "BTCUSDT", "status": "TRADING",
                 "quoteAsset": "USDT", "isSpotTradingAllowed": True}]})
        if request.url.path.endswith("24hr"):
            return httpx.Response(200, json=[{"symbol": "BTCUSDT", "quoteVolume": "40000000",
                                              "lastPrice": "90", "priceChangePercent": "4"}])
        if request.url.path.endswith("klines"):
            return httpx.Response(200, json=candles())
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(responder), base_url=m.BASE_URL)
    screen = m.SpotScreener(path=tmp_path / "scan.json")
    try:
        report = await screen.scan_once(client=client)
    finally:
        await client.aclose()
    assert report["source"] == "binance_spot_public_api"
    assert report["mode"] == "research_only"
    assert report["candidates"][0]["symbol"] == "BTCUSDT"
    assert report["candidates"][0]["score"] <= 100
    assert paths.count("/api/v3/klines") == 2
    assert json.loads(screen.path.read_text())["as_of"] == report["as_of"]
    assert screen.status()["trading_enabled"] is False


@pytest.mark.asyncio
async def test_failed_scan_does_not_replace_last_good_snapshot(tmp_path):
    path = tmp_path / "scan.json"
    path.write_text('{"as_of":"old","candidates":[]}', encoding="utf-8")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(429)), base_url=m.BASE_URL
    )
    screen = m.SpotScreener(path=path)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await screen.scan_once(client=client)
    finally:
        await client.aclose()
    assert json.loads(path.read_text())["as_of"] == "old"
    assert screen.status()["last_error"]


def test_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("VIBE_SPOT_SCREEN_ENABLED", raising=False)
    service = m.SpotScreener(path=tmp_path / "scan.json")
    service.start()
    assert service.status()["enabled"] is False
    assert service.status()["running"] is False


def test_interval_clamped(monkeypatch):
    monkeypatch.setenv("VIBE_SPOT_SCREEN_INTERVAL_SECONDS", "10")
    assert m.configured_interval() == 900
    monkeypatch.setenv("VIBE_SPOT_SCREEN_INTERVAL_SECONDS", "invalid")
    assert m.configured_interval() == 900
