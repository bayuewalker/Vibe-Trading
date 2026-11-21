# Crypto Spot automatic screening (research-only)

This feature introduces an independent, opt-in read-only market screener. It runs in-process alongside the existing Vibe-Trading API and does **not** enable the general scheduled research executor or the broker execution engine.

## Data source and TradingView boundary

This implementation **does not scrape or call the TradingView scanner**. TradingView's official MCP terms limit the tool to personal interactive questions and explicitly prohibit using its data for automated trading decisions, bots, signal products, and stored data feeds. See https://www.tradingview.com/mcp/docs#terms-of-use and https://www.tradingview.com/policies/.

Instead, this implementation uses Binance's public Spot API for automated research and includes TradingView chart links solely for manual review. Do not label these scans "TradingView data".

Data endpoints (read-only, no API key needed):

- `GET https://api.binance.com/api/v3/exchangeInfo`
- `GET https://api.binance.com/api/v3/ticker/24hr`
- `GET https://api.binance.com/api/v3/klines`

## Algorithm

Every 15 minutes, when opted in:

1. Select active, Spot-enabled Binance symbols quoted in `USDT`.
2. Exclude stablecoin/USDT and leveraged token suffixes; require at least 20 million USDT of 24-hour quote volume.
3. Evaluate the 12 most-liquid eligible pairs. Fetch the last 90 hourly and 4-hour candles; only **closed** candles enter the indicators.
4. Calculate EMA20/EMA50, RSI14, and relative 1H volume. Rank by explainable score 0–100 based on trends, RSI, volume and 24h change. Scores are **not calibrated probabilities**.
5. Persist up to 8 candidates as an atomic JSON snapshot under `VIBE_TRADING_HOME/screening/crypto_spot.json`.
6. Serve the status at authenticated `GET /api/spot-screener` and on `/spot-screener` in the Web UI.

A provider error retains the last successful snapshot and returns `last_error`. Stale snapshots are marked in the UI; no imaginary rows or fallback market prices are generated.

## Fly.io configuration — existing app `vibe-trading-bayue`

**Important:** The repository's existing `fly.toml` is still named `vibe-trading-staging`. Do not deploy it blindly to `vibe-trading-bayue` or overwrite its volume, Machine config, or secrets. First compare the actual app's deployed configuration, region, process count, mount, and rollback instructions.

Once the reviewed PR is deployed to the intended app and exactly one Machine serves the API:

```bash
fly secrets set VIBE_SPOT_SCREEN_ENABLED=1 --app vibe-trading-bayue
```

Optional (15 minutes is already the default, minimum 15 minutes):

```bash
fly secrets set VIBE_SPOT_SCREEN_INTERVAL_SECONDS=900 --app vibe-trading-bayue
```

A Fly secret update may restart a Machine. Do not enable multiple replicas/workers for this in-process poller. It does not use the general scheduler flag `VIBE_TRADING_ENABLE_SCHEDULER`; that flag should remain `0` until the existing scheduled-research jobs have been separately reviewed.

Open the authenticated `https://vibe-trading-bayue.fly.dev/spot-screener` route. Use the Web UI API auth key already configured for your server. A successful `/live` probe alone does not mean the screener is active. The screener API should report `enabled=true`, `running=true`, a recent `snapshot.as_of`, and `last_error=null`.

## Rollback

```bash
fly secrets set VIBE_SPOT_SCREEN_ENABLED=0 --app vibe-trading-bayue
```

The last screening snapshot stays on the volume for audit; it is not a live quote. Deploying a previous app release also removes the feature without deleting the volume.

## Scope / limitations

- No market orders, paper orders, stop loss, take profit, DCA, broker credentials, TradingView raw data, TradingView OAuth, or external LLM calls.
- No claim of a profitable strategy or live market performance.
- Binance public API may reject Fly egress IPs, throttle requests, or become unavailable; show an error and stop, do not fabricate results.
- Background scanning is only an opportunity discovery stage. AI research / risk agent orchestration can consume reviewed snapshots in a later independent PR after additional safety checks.
- English is the first-visit Web UI default; explicitly saved language preferences still take priority.
