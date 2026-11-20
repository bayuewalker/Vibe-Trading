# Deployment test report

Checked on branch `cursor/fly-io-staging-b1b5` from `61d20c9a` (2026-11-20). No Fly app was created. No provider key was sent to a model. No broker order was placed.

## Fly configuration

`flyctl` v0.5.14:

```text
flyctl config validate --config fly.toml
Error: no access token available. Please login with 'flyctl auth login'
```

The command talks to the Fly API, so it cannot pass or fail the file without an account. The local structural check does:

```text
.venv/bin/pytest agent/tests/test_fly_staging.py --tb=short -q
11 passed in 2.41s
```

That test reads `fly.toml` and asserts: region `sin`, build target `fly`, rolling deploys, internal port 8899, `force_https`, `auto_stop_machines = "off"`, `min_machines_running = 1`, the only HTTP check path is `/live`, VM `performance-2x` / 2 performance CPUs / 4 GB, one process group `app`, volume `vibe_data` mounted at `/home/vibe/.vibe-trading` (the same path as `VIBE_TRADING_HOME`), scheduler and channel autostart set to `0`, OpenRouter model `deepseek/deepseek-v4-pro`, and no API key or `VIBE_TRADING_TRUST_DOCKER_LOOPBACK` in the file.

## API process

The dev server already bound to `127.0.0.1:8899` returned:

```text
GET /live -> 200
{"status":"healthy","service":"Vibe-Trading API","timestamp":"2026-11-20T16:07:44.046638+00:00"}

GET /ready -> 200
{"status":"ready","service":"Vibe-Trading API","timestamp":"2026-11-20T16:07:44.053184+00:00"}
```

`/ready` was 200 because `agent/.env` contains the placeholder `OPENROUTER_API_KEY` from `agent/.env.example`. The probe only checks that a credential string is present. It does not call OpenRouter. With the key forced to an empty string in a separate process, `_provider_readiness()` returned:

```text
blank_key (False, 'LLM provider credential not configured')
blank_model (False, 'LLM model not configured')
```

No authenticated model call was made.

Authentication tests, which cover remote 403 without `API_AUTH_KEY` and bearer rejection on loopback once a key is set:

```text
.venv/bin/pytest agent/tests/test_security_auth_api.py --tb=line -q
62 passed in 3.00s
```

## Entrypoint

On this host, as root, `deploy/docker-entrypoint.sh` with `VIBE_TRADING_HOME=/tmp/fly-vol-test`:

- `id -u -n` printed `vibe`
- a write under `sessions/` succeeded
- the directory mode stayed `755` and the owner became `vibe`

With the directory mode `555` and owned by root, the same script printed `vibe cannot write /tmp/fly-vol-locked; refusing to start` and exited 1. `bash -n` accepted the script.

## Swarm

Preset discovery, task order, packaging, error surfacing, and retry tests:

```text
.venv/bin/pytest agent/tests/test_swarm_dag_gating.py \
  agent/tests/test_swarm_preset_inspect.py \
  agent/tests/test_swarm_presets_packaging.py \
  agent/tests/test_swarm_error_surfacing.py \
  agent/tests/test_swarm_retry.py --tb=line -q
40 passed in 5.13s
```

`test_fly_staging.py` also loads `equity_research_team`, `technical_analysis_panel`, `quant_strategy_desk`, `risk_committee`, and `crypto_research_lab` through `inspect_preset` and checks `depends_on` order. No swarm run called a model.

## Market data

Live network, not fixtures.

OKX public candles through `backtest.loaders.okx.DataLoader`, daily bars, 2024-01-02 through 2024-03-29. Indicators are the project functions `_compute_sma`, `_compute_rsi`, and `_compute_macd`. Drawdown is `src.quantlib.risk.max_drawdown_analysis` on a buy-and-hold of the close series (first close scaled to 1). This is not a signal-engine backtest.

| Symbol | Rows | First close | Last close | SMA 20 | RSI 14 | Buy-and-hold max drawdown |
| --- | --- | --- | --- | --- | --- | --- |
| BTC-USDT | 88 | 42985.9 | 70002.0 | 68372.91 | 59.53 | 0.1635 (peak 2024-01-08, trough 2024-01-22) |
| ETH-USDT | 88 | 2240.02 | 3536.2 | 3593.83 | 52.02 | 0.1808 (peak 2024-03-10, trough 2024-03-19, not recovered in the window) |

The yfinance loader maps `BTC-USDT` and `ETH-USDT` to Yahoo `BTC-USD` and `ETH-USD`. For 2024-01-02 through 2024-06-28 it returned 179 daily rows: BTC first close 44957.9688, last close 60320.1367; ETH first close 2355.8364, last close 3373.6360. Those are Yahoo USD prices, not OKX USDT prices.

`XAUUSD=X` and `XAU=X` returned HTTP 404 from Yahoo (`Quote not found`). The project symbol `XAUUSD.FX` is routed to MetaTrader 5, which was not called. `GC=F` (COMEX gold futures, a different instrument) returned 124 daily rows on the same window, first close 2073.3999, last close 2339.6001. That is not an XAUUSD quote.

Fixture-only metric tests, separate from the rows above:

```text
.venv/bin/pytest agent/tests/test_metrics.py --tb=line -q
77 passed in 0.42s
```

## Frontend

Unchanged by this branch. Re-run on this tree:

```text
npm run test:run
Test Files  91 passed (91)
Tests  844 passed (844)

npm run build
tsc -b && vite build
built in 1.62s
```

Vite printed the existing chunk-size warning. The build exited 0.

## Backend suite already run on main

Before these Fly files existed, a full `pytest` on this machine (ignoring `agent/tests/e2e_backtest` and `test_e2e_harness_v2`) reported 21185 passed, 16 failed, 176 skipped. Re-running those 16 outside a terminal multiplexer left one failure. That test still fails on this branch and does not import the Fly files:

```text
agent/tests/test_chat_llm_lifecycle.py::test_close_keeps_langchain_cached_transport_open_for_a_sibling_adapter
AssertionError: two AsyncHttpxClientWrapper objects are not the same object
```

It asserts langchain-openai reuses one cached async transport. Current langchain returns a new wrapper. This change does not touch `src/providers` or that test.

## Docker image

Docker 29.1.3. Both builds exited 0.

```text
docker build --target fly -t vibe-trading:fly .
Successfully built 451dda5a0703
Successfully tagged vibe-trading:fly

docker build -t vibe-trading:runtime .
Successfully built 5f1885386535
Successfully tagged vibe-trading:runtime
```

`vibe-trading:runtime` inspect: `user=vibe`, no entrypoint, command `vibe-trading serve --host 0.0.0.0 --port 8899`. That is the image Compose keeps using.

`vibe-trading:fly` inspect: `user=root`, entrypoint `/usr/local/bin/vibe-trading-entrypoint`, same command. `docker run --rm vibe-trading:fly id -u -n` printed `vibe`. The running server process (`/proc/1/status`) was uid 1000, which is `vibe` inside this image. Its inheritable, effective, and ambient capability mask was `00000000000000c0` (`CAP_SETGID` and `CAP_SETUID` only).

Two containers sharing a bind mount of `/tmp/fly-data-proof` wrote and then read `sessions/marker.txt` (`survived`). The directory mode stayed `755`. A read-only mount of the data directory printed `vibe cannot write /home/vibe/.vibe-trading; refusing to start` and exited 1.

The Fly image was started with an empty `OPENROUTER_API_KEY`, scheduler flags `0`, and port `127.0.0.1:18899` published to container port 8899. From the Docker bridge address `172.17.0.1` (not loopback):

```text
GET /live -> 200
{"status":"healthy","service":"Vibe-Trading API","timestamp":"2026-11-20T16:16:48.001158+00:00"}

GET /ready -> 503
{"detail":"LLM provider credential not configured"}

POST /api/paper-accounts -> 403
{"detail":"Settings writes require API_AUTH_KEY or a local loopback client"}
```

Startup logged that the frontend is served from `/app/frontend/dist`, warned that `0.0.0.0` has no `API_AUTH_KEY`, and marked OKX and yfinance reachable. The LLM preflight line was also OK. That check only GETs the provider base URL (`https://openrouter.ai`) and does not send a key or a prompt. `/ready` is the credential result, and it was 503. No model call was made.

## Review follow-up

After the requested changes (fail closed when sandbox capabilities are missing, and `--ha=false` plus a single-Machine check):

```text
.venv/bin/pytest agent/tests/test_fly_staging.py --tb=short -q
16 passed in 2.20s
```

The new entrypoint test puts a fake `id` and `setpriv` on `PATH`, forces the capability probe to fail, and asserts the server command does not run. A second test asserts the command runs only when the probe is allowed to keep `CAP_SETUID` and `CAP_SETGID`. `deploy/check_single_machine.py` accepts one attached `app` Machine and rejects a second `app` Machine. Its output does not include fixture secret values.

The Fly image was rebuilt after that entrypoint change:

```text
docker build --target fly -t vibe-trading:fly .
Successfully built e1993e3d20f1
```

`docker run --rm vibe-trading:fly id -u -n` printed `vibe`. A process started by the entrypoint was uid 1000 with ambient capability mask `00000000000000c0`. The script inside the image contains the fail-closed refusal and does not contain the previous "start without those capabilities" path. No Fly app was created.
