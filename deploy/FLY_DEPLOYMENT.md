# Fly.io staging deployment

This profile runs the existing Vibe-Trading image as one always-on Machine in Singapore. It does not add a second scheduler, a live broker, or a MetaTrader 5 terminal.

The repository rejects a tracked top-level `docs/` directory (`tools/ci_private_files_gate.py`). These operator notes live under `deploy/` for that reason.

`fly deploy` creates a billable Machine and, on first use, a 10 GB volume. Do not run it until that spend is authorized.

## What is deployed

```text
Browser --HTTPS--> Fly proxy :443
                      |
                      +--> Machine (performance-2x, 2 vCPU, 4 GB, region sin)
                             vibe-trading serve --host 0.0.0.0 --port 8899
                             user: vibe (entrypoint starts as root only to chown the volume)
                             volume vibe_data -> /home/vibe/.vibe-trading
```

`fly.toml` selects the Dockerfile stage `fly`. A plain `docker build .` still produces the `runtime` stage, so Compose is unchanged and stays `USER vibe`.

The Fly stage's entrypoint (`deploy/docker-entrypoint.sh`):

1. Creates `/home/vibe/.vibe-trading` when it is missing.
2. Gives files in that directory to `vibe` when it is allowed to `chown`.
3. Refuses to start if `vibe` cannot write the directory.
4. Refuses to start unless `setpriv` can keep `CAP_SETUID` and `CAP_SETGID` while switching to `vibe`.
5. Execs the server as `vibe` with only that capability pair, so generated strategy code can still drop to `vibe-sandbox`.

If those capabilities are refused, the entrypoint exits before the server starts. Generated strategy code does not run as `vibe`. The entrypoint does not make the volume world-writable and does not turn the uid drop off.

## Persistent state

`VIBE_TRADING_HOME=/home/vibe/.vibe-trading` is the mount point. That is also `~/.vibe-trading` for the `vibe` user, so both the runtime-root helpers and home-relative paths land on the volume:

| Data | Path on the volume |
| --- | --- |
| Provider settings file written by the Web UI | `.env` |
| Agent memory, skills, hypotheses, shadow accounts | under this directory |
| Chat sessions | `sessions/` |
| Run artifacts and backtests | `runs/` |
| Uploads | `uploads/` |
| Swarm run history | `swarm/runs/` |
| User swarm presets | `swarm/presets/` |

The Machine root filesystem is ephemeral. Anything outside the volume is lost on deploy or restart.

Fly environment variables and secrets are already set when the process starts. `load_dotenv` does not override them. A value in `fly.toml` `[env]` or in `fly secrets` wins over the same key saved from the Web UI. Change the model with `fly secrets set` or by editing `fly.toml`, then redeploy.

## Required secrets

Generate the API key locally. Do not commit it and do not paste it into a log.

```bash
openssl rand -hex 32
```

From a checkout of this revision, after `fly auth login` and after the app name in `fly.toml` matches an app your account may use:

```bash
fly apps create vibe-trading-staging --org <org>
fly volumes create vibe_data --region sin --size 10 --app vibe-trading-staging
fly secrets set \
  API_AUTH_KEY="<the hex key>" \
  OPENROUTER_API_KEY="<provider key>" \
  --app vibe-trading-staging
fly deploy --ha=false --app vibe-trading-staging
python deploy/check_single_machine.py --app vibe-trading-staging
```

`--ha=false` is required on the first deploy. `min_machines_running = 1` is a floor, not a cap, and Fly otherwise creates a second Machine for high availability. A second Machine cannot share `vibe_data`. The checker exits 0 only when exactly one `app` Machine exists and that Machine is the one attached to `vibe_data`. Leave the scheduler at `0` when the checker exits non-zero.

`initial_size` in `fly.toml` also asks `fly deploy` to create `vibe_data` when it is missing. Creating the volume yourself makes the region explicit.

Supported provider variables already read by the app can be set the same way (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, and the matching `*_BASE_URL`). The staging default is OpenRouter with `deepseek/deepseek-v4-pro`, which matches `agent/.env.example`. No key is baked into the image.

## Health

| Probe | Role | Expected before a provider key is set |
| --- | --- | --- |
| `GET /live` (alias `GET /health`) | Fly HTTP check and the image `HEALTHCHECK` | `200` `{"status":"healthy"}` |
| `GET /ready` | Operator check that a provider, model, and credential are configured | `503` with a non-sensitive reason |

`/ready` is not the Machine health check. A 503 there means "not ready to call a model", not "restart the Machine".

After secrets are present:

```bash
curl -fsS "https://<app>.fly.dev/live"
curl -i "https://<app>.fly.dev/ready"
```

Open `https://<app>.fly.dev`, enter `API_AUTH_KEY` once in Settings, and confirm the header shows OpenRouter and the configured model. That screen is configuration evidence. It is not evidence that a model call succeeded.

A real model call is a chat or swarm run that returns provider-authored text. Do not treat a mocked test or `/ready` as that call.

## Single instance

The volume attaches to one Machine. `min_machines_running` is 1, autostop is off, and the deploy strategy is `rolling` because bluegreen and canary cannot attach the same volume. The first deploy must pass `--ha=false`. After every deploy, confirm the count before any scheduled work:

```bash
fly deploy --ha=false --app vibe-trading-staging
python deploy/check_single_machine.py --app vibe-trading-staging
fly scale count 1 --app vibe-trading-staging
```

`check_single_machine.py` reads `fly machines list` and `fly volumes list`. It exits 0 only when the app process group has exactly one Machine and `vibe_data` is attached to that Machine. Do not set `VIBE_TRADING_ENABLE_SCHEDULER` or `VIBE_BR_SYNC_ENABLED` to anything other than `0` unless that command exits 0. Those flags ship as `0`, so this Machine does not fire scheduled research or the Brazilian data sync. Turning the scheduler on also spends model tokens.

`VIBE_TRADING_TRUST_DOCKER_LOOPBACK` is left unset. Setting it would treat the proxy hop as a local operator and skip `API_AUTH_KEY`.

## Paper trading only

Do not set broker credentials (`IBKR_*`, `LONGBRIDGE_*`, exchange trading keys, or an MT5 login). Do not call live-order or mandate-commit endpoints. Paper accounts and backtests do not send broker orders.

MetaTrader 5 is not part of this deployment. The official `MetaTrader5` Python package needs a Windows terminal on the same machine. A Fly Linux Machine cannot host that terminal. A future bridge would be a separate Windows host that accepts only authenticated, audited requests from this app and never shares its broker password with the Fly image. That bridge is not implemented here.

## Rollback

```bash
fly releases --app vibe-trading-staging
fly deploy --ha=false --image <previous-image> --app vibe-trading-staging
python deploy/check_single_machine.py --app vibe-trading-staging
```

Volume snapshots are retained for 5 days (`snapshot_retention`). Restoring a snapshot is a Fly volume operation and replaces the data directory with that snapshot. Take a snapshot before a risky data change:

```bash
fly volumes list --app vibe-trading-staging
fly volumes snapshots create <volume-id> --app vibe-trading-staging
```

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `flyctl config validate` asks for a login | That command sends the file to the Fly API. `fly auth login` is required. `agent/tests/test_fly_staging.py` checks the file locally and does not create an app. |
| Deploy fails, volume missing | `fly volumes list`. Create `vibe_data` in `sin`. |
| Machine restarts immediately | `fly logs`. The entrypoint exits if `vibe` cannot write the volume. |
| Browser says an API key is required | Set `API_AUTH_KEY`, redeploy, enter the same value in Settings. |
| `/live` is 200 and `/ready` is 503 | Provider, model, or credential is missing. The Machine should stay up. |
| Web UI model edit does not stick | A Fly secret or `[env]` value overrides the settings file. |
| Machine exits on boot mentioning `vibe-sandbox` | The host refused `CAP_SETUID`/`CAP_SETGID`. The entrypoint stops on purpose. Do not chmod the volume to 0777 and do not remove the probe. |
| Two Machines, one volume | Redeploy with `--ha=false`, then `fly scale count 1`. Run `python deploy/check_single_machine.py` before enabling the scheduler. |
