# TradingView-first automatic research — rollout guide

## Current scope

This change adds the English first-visit UI default and a **research-only**
TradingView-first scheduled playbook. It **does not** enable a scheduler or
authenticate a TradingView account automatically, and does not place orders.
Full unattended running requires an initial operator OAuth connection and
persisted refresh tokens.

### Why the official MCP

TradingView's documented server is `https://mcp.tradingview.com/mcp`.
It supports `run_screener` and read-only screener/technical tools, but **OAuth
2.1 account authorization is mandatory**. A Fly API token or an LLM API key
does not authenticate TradingView. Tool calls are subject to TradingView plan
quotas (which may be too low for frequent polling).

**Do not** scrape undocumented TradingView screeners or reuse a browser cookie.

## Read-only connector definition

The audited template is `deploy/tradingview-agent.example.json`, intended
to be merged into the Fly volume's agent config, located at
`/home/vibe/.vibe-trading/agent.json`. Preserve existing settings and other
connectors; do not replace that file blindly. All exposed tools in the
template are official TradingView **read-only** tools.

The OAuth cache is on the persistent volume at
`/home/vibe/.vibe-trading/tradingview/oauth`. Keep permissions private; do
not commit OAuth tokens, copy them to Actions logs, or put credentials in URL
query strings.

**Critical blocker:** Existing FastMCP interactive OAuth normally opens a
localhost browser callback on the executing host. A remote Fly Machine has no
operator browser. Do not attempt to bypass this with a static access token
or a publicly exposed OAuth callback. An authenticated operator-facing
OAuth connect/redirect/callback flow must be implemented and tested before
claiming unattended TradingView access from Fly.

## Scheduler / playbook

The bundled playbook is `tradingview-market-scan`, visible in the scheduled
research playbook list after deployment. Suggested cadence: every 4 hours
(UTC), to avoid rapidly spending TradingView API quotas. It uses the
official screener as the **first** market data source; missing or partial
data must lead to a `BLOCKED` or `NO_TRADE` report rather than invented
signals.

The current Fly deployment configuration ships:
- `VIBE_TRADING_ENABLE_SCHEDULER=0`
- `VIBE_BR_SYNC_ENABLED=0`
- `VIBE_TRADING_CHANNELS_AUTO_START=0`

This is deliberate. Do not switch on the scheduled research executor until:

1. The exact Fly app is identified (the operator may have deployed under a
   different app name than `fly.toml`).
2. Only one app Machine owns one persistent volume; run
   `python deploy/check_single_machine.py --app <actual-app-name>`.
3. TradingView OAuth sign-in and refresh have been demonstrated across a
   Machine restart.
4. A **real** screener run succeeds without a broker connection.
5. A research-only playbook job is created and persisted.
6. Background runs report status, errors and timestamps, with quota-aware
   intervals and no overlapping runs.
7. LLM readiness and agent tool permissions are verified; trade execution
   permissions remain absent.

Only after those gates pass may the operator enable the scheduler, first on
one Machine. The screener never submits live or paper orders. Any future
execution flow requires a separate deterministic risk gate and approval.

## Validation plan

- Frontend tests and build: English first-visit default and explicit user
  preference persistence.
- Backend tests: playbook metadata, default cadence, source-required
  fail-closed instructions and scheduled-job persistence.
- Real source acceptance: signed-in TradingView MCP screener returns
  exchange-qualified symbols, timestamps and a non-partial result.
- Negative acceptance: missing/expired OAuth, 429, 5xx, empty/partial
  results -> no candidate signal and no broker call.
- Operational acceptance: verify periodic execution, durable status, daily
  request cap, single-Machine restart recovery and no duplicate run.
- UI acceptance: show source, last scan, next scan, screened assets, accepted
  or rejected candidates, data gaps and paper-only mode.

## Important

The user-facing deployed site may be hosted at an app name different from
`vibe-trading-staging`; **never assume a merge automatically deploys it**.
Inspect the actual Fly app and deployment process before updating live
settings. A GitHub PR merged to `main` is code delivery, not verification
that the production/staging site received that build.
