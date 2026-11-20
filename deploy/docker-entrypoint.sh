#!/bin/sh
# Fly.io entrypoint. The image USER for this target is root so a fresh volume
# can be handed to the unprivileged `vibe` account. The API process is never
# left running as root.
#
# CAP_SETUID and CAP_SETGID are kept across the drop when the platform allows
# it. That is the same pair docker-compose grants so runner.py can execute
# generated strategy code as `vibe-sandbox`. If the platform refuses those
# capabilities, the server still starts: runner.py logs a warning and keeps
# the AST scrubber. This script does not chmod the data directory world-writable
# and does not disable that scrubber.
set -eu

DATA_DIR="${VIBE_TRADING_HOME:-/home/vibe/.vibe-trading}"

ensure_data_dir() {
  mkdir -p "$DATA_DIR"
}

chown_data_dir() {
  # Best-effort. Compose drops CAP_CHOWN; its init container owns this step.
  # A failure here must not chmod the tree or skip the uid drop.
  find "$DATA_DIR" ! -user vibe -exec chown vibe:vibe {} + 2>/dev/null || true
}

vibe_can_write() {
  setpriv --reuid=vibe --regid=vibe --init-groups -- /usr/bin/test -w "$DATA_DIR"
}

drop_to_vibe() {
  export HOME=/home/vibe
  export USER=vibe
  export LOGNAME=vibe
  if setpriv --reuid=vibe --regid=vibe --init-groups \
    --inh-caps=-all,+setuid,+setgid \
    --ambient-caps=-all,+setuid,+setgid \
    -- true 2>/dev/null; then
    exec setpriv --reuid=vibe --regid=vibe --init-groups \
      --inh-caps=-all,+setuid,+setgid \
      --ambient-caps=-all,+setuid,+setgid \
      -- "$@"
  fi
  echo "setpriv could not keep SETUID/SETGID; starting as vibe without those capabilities" >&2
  echo "generated-code subprocesses will not drop to vibe-sandbox; the AST scrubber stays active" >&2
  exec setpriv --reuid=vibe --regid=vibe --init-groups -- "$@"
}

if [ "$(id -u)" = "0" ]; then
  command -v setpriv >/dev/null 2>&1 || {
    echo "setpriv is required to drop root before serving" >&2
    exit 1
  }
  ensure_data_dir
  chown_data_dir
  if ! vibe_can_write; then
    echo "vibe cannot write ${DATA_DIR}; refusing to start" >&2
    exit 1
  fi
  drop_to_vibe "$@"
fi

ensure_data_dir 2>/dev/null || true
if ! test -w "$DATA_DIR"; then
  echo "cannot write ${DATA_DIR} as uid $(id -u); refusing to start" >&2
  exit 1
fi
exec "$@"
