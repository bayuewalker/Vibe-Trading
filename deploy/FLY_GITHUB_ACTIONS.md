# Fly.io staging deployment via GitHub Actions

Workflow: `.github/workflows/fly-staging.yml`

The first run is triggered only when this workflow is merged into `main`. Subsequent deployments are manual via **Actions → Deploy Vibe-Trading to Fly.io staging → Run workflow**. It will not deploy on unrelated commits.

## GitHub repository secrets

Configure under **Settings → Secrets and variables → Actions**:

| Name | Required | Purpose |
| --- | --- | --- |
| `FLY_API_TOKEN` | Yes | Fly access with permission to create an app and deploy to the intended organization |
| `VIBE_API_AUTH_KEY` | Recommended | Stable 64-character or stronger random API auth key for remote dashboard login |
| `OPENROUTER_API_KEY` | Optional until LLM testing | Real model provider calls |

**Do not reuse `FLY_API_TOKEN` as `VIBE_API_AUTH_KEY`.** Do not commit secrets or print them in Action logs.

If `VIBE_API_AUTH_KEY` is missing on first deploy, the workflow generates an unrecoverable, strong random `API_AUTH_KEY` stored only in Fly secrets. The remote API remains authenticated, but **dashboard login will not be possible until you set a known `VIBE_API_AUTH_KEY` secret in GitHub and rerun the workflow** (or rotate `API_AUTH_KEY` directly with Fly).

If `OPENROUTER_API_KEY` is absent, the server may be alive while `/ready` returns 503. That is expected, and does not prove real AI inference.

## Provisioning and billing

This workflow may create a billable Fly.io application named `vibe-trading-staging` with one `performance-2x` Machine (2 vCPU, 4 GB RAM) in Singapore and a 10 GB volume. The `--ha=false` deploy flag prevents creating a spare Machine. The configuration is intended to stay always on; check actual Fly rates and available account credits before approving charges.

The workflow uses `fly.toml` as source of truth, performs a single-instance/volume attachment check using `deploy/check_single_machine.py`, and verifies `https://vibe-trading-staging.fly.dev/live`.

The workflow does not set broker credentials or turn on live execution, the scheduler, Brazilian synchronization, or auto-start channels.

## Run results and blockers

Read the **GitHub Actions run and job summary** for the result, commands, and any errors. A workflow or file existing in GitHub is *not* evidence of successful Fly deployment.

Common blockers:

- `FLY_API_TOKEN` missing, invalid, or lacks permission to create Fly apps.
- App name `vibe-trading-staging` already belongs to another Fly account.
- Billing setup, VM capacity, regional availability, or image build failure.
- Generated-code sandbox capabilities unavailable on the Fly Machine (startup must fail closed).
- Missing `OPENROUTER_API_KEY` (`/ready` may return 503).

If the Fly application is created but deploy fails, inspect its state in the Fly dashboard and the Actions logs before retrying. Do not turn on live trading, and do not set exchange or broker trading credentials.

For manual setup, rollback, safety boundaries, and configuration details see [FLY_DEPLOYMENT.md](FLY_DEPLOYMENT.md) and [DEPLOYMENT_SECURITY.md](DEPLOYMENT_SECURITY.md).
