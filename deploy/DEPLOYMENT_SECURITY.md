# Deployment security

Staging on Fly.io uses the controls already in the application. This profile does not turn any of them off to make the platform fit.

## Authentication

Fly's proxy connects from the private network, not from 127.0.0.1. With no `API_AUTH_KEY`, remote requests receive `403` and the message `API_AUTH_KEY is required for non-local API access`. With a key set, every caller, including loopback, must send `Authorization: Bearer <key>` (`src/api/security.py`). The Web UI stores that key in the browser and sends it on later requests.

Set the key only as a Fly secret:

```bash
fly secrets set API_AUTH_KEY="$(openssl rand -hex 32)" --app vibe-trading-staging
```

`fly.toml` does not contain `API_AUTH_KEY`, provider keys, or `VIBE_TRADING_TRUST_DOCKER_LOOPBACK`. That last variable would trust the Docker/Fly gateway as a local operator. Leave it unset.

HTTPS is terminated by Fly (`force_https = true`). The app listens on port 8899 inside the Machine.

## Credentials

Provider keys (`OPENROUTER_API_KEY` and the other keys the existing provider layer already reads) are Fly secrets. They become environment variables before the process starts. `load_dotenv` is called with `override=False`, so a secret is not replaced by a value in the on-volume `.env`.

The readiness probe (`GET /ready`) reports `LLM provider credential not configured` when the selected provider's key is missing. It does not include the key. Do not add request logging that prints `Authorization` or the settings file.

The Web UI can write `.env` on the volume with mode `0600` (`_atomic_write_secret`). That file is persistent. It is not in the image and must not be committed.

## Process identity and the code sandbox

The default image (`runtime`) keeps `USER vibe`. The Fly build target starts as root so a newly created volume, which Fly formats as root, can be given to `vibe`. `deploy/docker-entrypoint.sh` then execs the server as `vibe`. If that drop fails, the entrypoint exits. It does not keep serving as root.

`vibe-sandbox` (uid 10001, no home, no shell) is still the account `runner.py` tries to use for generated strategy code. The entrypoint asks `setpriv` to keep only `CAP_SETUID` and `CAP_SETGID` across the drop, which is the same pair `docker-compose.yml` adds for that purpose. Those capabilities let `vibe` switch to `vibe-sandbox`. They do not let `vibe-sandbox` become root.

When the platform refuses the capability, the entrypoint exits and the server does not start. Generated strategy code therefore does not run as `vibe`, the user that owns `.env`, sessions, and the rest of the volume. This profile does not chmod the data directory `0777` and does not set a switch that skips the sandbox.

## Persistent files

The volume holds sessions, runs, uploads, swarm history, and memory. Uploads stay under `VIBE_TRADING_HOME/uploads`. Treat that directory as user content: the existing upload routes are unchanged.

Shell tools stay on the application's existing policy (`VIBE_TRADING_BASH_POLICY` and the shell-tool flag in the env schema). This deployment does not set those to a more permissive value.

## Trading

This Machine is for research and paper trading.

- No live-broker environment variables are set.
- The scheduler is off, so a restart does not start a second copy of a scheduled run. Keep the app at one Machine so two processes cannot share the volume and double-submit work.
- Mandate commit and live-order routes are unchanged. Do not authorize a mandate from this staging app.
- Paper-account routes simulate fills. They do not send exchange orders.

## MetaTrader 5

The `MetaTrader5` package talks to a local Windows terminal. A Linux Fly Machine cannot run that terminal, and this profile does not try. A later design would put the terminal on a separate Windows host, authenticate every request, and keep the broker password on that host. It is not part of this change.

## Shutdown

Fly sends `SIGTERM` and waits 30 seconds (`kill_signal` / `kill_timeout`) before a hard stop. The entrypoint execs the server, so the signal reaches `vibe-trading serve`.

## What this document does not claim

No Fly organization was authenticated in the change that added this file, and no Machine was created. A green `/live` on a future deploy shows the process is up. It does not show that a model accepted a prompt or that a broker connection exists.
