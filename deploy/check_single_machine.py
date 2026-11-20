#!/usr/bin/env python3
"""Fail unless one app Machine is the sole attachment of volume vibe_data.

`min_machines_running = 1` does not stop Fly from creating a second Machine
on the first deploy. Scheduled work stays disabled until this command exits 0.
The command prints counts and Machine ids only. It does not print Machine
config, environment, or secrets, and it does not create or scale anything.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from typing import Any


VOLUME_NAME = "vibe_data"
PROCESS_GROUP = "app"
_DEAD_STATES = {"destroyed", "destroying"}


def evaluate(
    machines: list[dict[str, Any]],
    volumes: list[dict[str, Any]],
    *,
    volume_name: str = VOLUME_NAME,
    process_group: str = PROCESS_GROUP,
) -> tuple[bool, str]:
    """Return whether the inventory is exactly one attached app Machine.

    Args:
        machines: Objects shaped like `fly machines list --json`.
        volumes: Objects shaped like `fly volumes list --json`.
        volume_name: Fly volume name that must be the only data disk.
        process_group: Process group that must contain one Machine.

    Returns:
        `(ok, reason)`. `reason` is safe to print. It contains ids and counts.
    """
    named = [item for item in volumes if item.get("name") == volume_name]
    if len(named) != 1:
        return False, f"expected exactly one volume named {volume_name}, found {len(named)}"
    attached = str(named[0].get("attached_machine_id") or "")
    if not attached:
        return False, f"volume {volume_name} is not attached to a Machine"

    live = [item for item in machines if _is_live_group(item, process_group)]
    ids = [str(item.get("id") or "") for item in live]
    if len(live) != 1:
        shown = ",".join(ids) or "(none)"
        return False, (
            f"expected exactly one {process_group} Machine, found {len(live)} ({shown})"
        )
    only = ids[0]
    if only != attached:
        return False, (
            f"volume {volume_name} is attached to {attached}, not the only "
            f"{process_group} Machine {only}"
        )
    return True, f"one {process_group} Machine {only} owns {volume_name}"


def _is_live_group(machine: dict[str, Any], process_group: str) -> bool:
    state = str(machine.get("state") or "").lower()
    if state in _DEAD_STATES:
        return False
    config = machine.get("config") if isinstance(machine.get("config"), dict) else {}
    metadata = config.get("metadata") if isinstance(config.get("metadata"), dict) else {}
    group = str(metadata.get("fly_process_group") or PROCESS_GROUP)
    return group == process_group


def _as_list(payload: Any, plural_key: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get(plural_key), list):
        items = payload[plural_key]
    else:
        raise ValueError(f"expected a list of {plural_key}")
    if not all(isinstance(item, dict) for item in items):
        raise ValueError(f"{plural_key} entries must be objects")
    return items


def _read_json(path: str, plural_key: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        return _as_list(json.load(handle), plural_key)


def _flyctl() -> str:
    return shutil.which("flyctl") or shutil.which("fly") or ""


def _fly_json(binary: str, app: str, kind: str) -> list[dict[str, Any]]:
    completed = subprocess.run(
        [binary, kind, "list", "--app", app, "--json"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit {completed.returncode}"
        raise RuntimeError(f"fly {kind} list failed: {tail}")
    key = "machines" if kind == "machines" else "volumes"
    return _as_list(json.loads(completed.stdout or "null"), key)


def main(argv: list[str] | None = None) -> int:
    """Check a live app, or JSON fixtures passed by tests."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", default="vibe-trading-staging")
    parser.add_argument("--machines", help="Fixture file instead of fly machines list")
    parser.add_argument("--volumes", help="Fixture file instead of fly volumes list")
    args = parser.parse_args(argv)
    if bool(args.machines) != bool(args.volumes):
        print("pass both --machines and --volumes, or neither", file=sys.stderr)
        return 2
    try:
        if args.machines:
            machines = _read_json(args.machines, "machines")
            volumes = _read_json(args.volumes, "volumes")
        else:
            binary = _flyctl()
            if not binary:
                print("flyctl is not installed; refusing to treat the app as single-instance", file=sys.stderr)
                return 1
            machines = _fly_json(binary, args.app, "machines")
            volumes = _fly_json(binary, args.app, "volumes")
        ok, reason = evaluate(machines, volumes)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"single-instance check failed: {exc}", file=sys.stderr)
        return 1
    print(reason)
    if not ok:
        print(
            "leave VIBE_TRADING_ENABLE_SCHEDULER and VIBE_BR_SYNC_ENABLED at 0",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
