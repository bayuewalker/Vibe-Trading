"""Fly.io staging profile stays single-instance, secret-free, and paper-only."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from src.swarm.presets import inspect_preset, list_presets

ROOT = Path(__file__).resolve().parents[2]
FLY_TOML = ROOT / "fly.toml"
DOCKERFILE = ROOT / "Dockerfile"
ENTRYPOINT = ROOT / "deploy" / "docker-entrypoint.sh"
SINGLE_MACHINE = ROOT / "deploy" / "check_single_machine.py"

# Presets that cover the research desks this staging profile must load.
# inspect_preset does not call an LLM.
RESEARCH_PRESETS = (
    "equity_research_team",
    "technical_analysis_panel",
    "quant_strategy_desk",
    "risk_committee",
    "crypto_research_lab",
)

SECRET_MARKERS = ("sk-", "sk-or-", "sk-ant-", "nvapi-", "gho_")


def _fly() -> dict:
    return tomllib.loads(FLY_TOML.read_text(encoding="utf-8"))


def test_fly_toml_is_single_instance_https_staging():
    cfg = _fly()
    assert cfg["primary_region"] == "sin"
    assert cfg["build"]["dockerfile"] == "Dockerfile"
    assert cfg["build"]["build-target"] == "fly"
    assert cfg["deploy"]["strategy"] == "rolling"

    service = cfg["http_service"]
    assert service["internal_port"] == 8899
    assert service["force_https"] is True
    assert service["auto_stop_machines"] == "off"
    assert service["auto_start_machines"] is True
    assert service["min_machines_running"] == 1
    assert service["processes"] == ["app"]
    assert [check["path"] for check in service["checks"]] == ["/live"]
    assert all(check["path"] != "/ready" for check in service["checks"])

    vm = cfg["vm"][0]
    assert vm["size"] == "performance-2x"
    assert vm["cpus"] == 2
    assert vm["cpu_kind"] == "performance"
    assert str(vm["memory"]).lower() in {"4gb", "4096"}
    assert vm["processes"] == ["app"]
    assert list(cfg["processes"]) == ["app"]

    mounts = cfg["mounts"]
    assert mounts["source"] == "vibe_data"
    assert mounts["destination"] == "/home/vibe/.vibe-trading"
    assert mounts["processes"] == ["app"]


def test_fly_env_has_no_secrets_and_keeps_trading_idle():
    env = _fly()["env"]
    assert env["VIBE_TRADING_HOME"] == "/home/vibe/.vibe-trading"
    assert env["VIBE_TRADING_HOME"] == _fly()["mounts"]["destination"]
    assert env["LANGCHAIN_PROVIDER"] == "openrouter"
    assert env["LANGCHAIN_MODEL_NAME"].startswith("deepseek/")
    assert env["OPENROUTER_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert env["VIBE_TRADING_ENABLE_SCHEDULER"] == "0"
    assert env["VIBE_BR_SYNC_ENABLED"] == "0"
    assert env["VIBE_TRADING_CHANNELS_AUTO_START"] == "0"
    assert "API_AUTH_KEY" not in env
    assert "OPENROUTER_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "VIBE_TRADING_TRUST_DOCKER_LOOPBACK" not in env

    blob = FLY_TOML.read_text(encoding="utf-8")
    for marker in SECRET_MARKERS:
        assert marker not in blob


def _single_machine():
    spec = importlib.util.spec_from_file_location("check_single_machine", SINGLE_MACHINE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_fly_dockerfile_target_does_not_replace_the_default_image():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "FROM runtime AS fly" in text
    assert text.strip().endswith("FROM runtime")
    assert "USER vibe" in text
    assert "vibe-sandbox" in text
    assert "/usr/local/bin/vibe-trading-entrypoint" in text
    fly_stage = text.split("FROM runtime AS fly", 1)[1].split("\nFROM runtime", 1)[0]
    assert "USER root" in fly_stage


def test_entrypoint_refuses_world_writable_data_and_keeps_sandbox_caps():
    script = ENTRYPOINT.read_text(encoding="utf-8")
    assert re.search(r"^\s*chmod\b", script, re.MULTILINE) is None
    assert "0777" not in script
    assert "777" not in script
    assert "setpriv" in script
    assert "+setuid,+setgid" in script
    assert "vibe-sandbox" in script
    assert "refusing to start" in script
    assert "cannot drop generated code to vibe-sandbox" in script
    assert "without those capabilities" not in script
    assert "fly deploy --ha=false" in (ROOT / "deploy" / "FLY_DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "--ha=false" in FLY_TOML.read_text(encoding="utf-8")
    mode = ENTRYPOINT.stat().st_mode
    assert mode & stat.S_IXUSR


def test_entrypoint_runs_a_command_when_the_data_dir_is_writable(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    marker = tmp_path / "ran"
    env = os.environ.copy()
    env["VIBE_TRADING_HOME"] = str(data)
    completed = subprocess.run(
        [str(ENTRYPOINT), "python3", "-c", f"open({str(marker)!r}, 'w').write('ok')"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert marker.read_text(encoding="utf-8") == "ok"


def test_entrypoint_refuses_an_unwritable_data_dir(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    data.chmod(0o500)
    env = os.environ.copy()
    env["VIBE_TRADING_HOME"] = str(data)
    try:
        completed = subprocess.run(
            [str(ENTRYPOINT), "python3", "-c", "print('should-not-run')"],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        # Root bypasses mode bits, so this assertion is for an unprivileged user.
        if os.geteuid() == 0:
            pytest.skip("root can write a 0500 directory")
        assert completed.returncode != 0
        assert "refusing to start" in completed.stderr
        assert "should-not-run" not in completed.stdout
    finally:
        data.chmod(0o700)


def _install_fake_root_tools(tmp_path: Path, *, deny_caps: bool) -> dict[str, str]:
    """PATH tools that make the entrypoint believe it is root."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "id").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-u" ]; then echo 0; exit 0; fi\n'
        "echo unexpected-id >&2\n"
        "exit 99\n",
        encoding="utf-8",
    )
    (bindir / "setpriv").write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['SETPRIV_LOG'], 'a', encoding='utf-8') as handle:\n"
        "    handle.write(' '.join(args) + '\\n')\n"
        "if any(arg.startswith('--ambient-caps') or arg.startswith('--inh-caps') for arg in args):\n"
        "    if os.environ.get('SETPRIV_DENY_CAPS') == '1':\n"
        "        sys.stderr.write('cap denied\\n')\n"
        "        raise SystemExit(1)\n"
        "if '--' not in args:\n"
        "    raise SystemExit(0)\n"
        "cmd = args[args.index('--') + 1:]\n"
        "os.execvp(cmd[0], cmd)\n",
        encoding="utf-8",
    )
    os.chmod(bindir / "id", 0o755)
    os.chmod(bindir / "setpriv", 0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    env["SETPRIV_LOG"] = str(tmp_path / "setpriv.log")
    env["SETPRIV_DENY_CAPS"] = "1" if deny_caps else "0"
    env["VIBE_TRADING_HOME"] = str(tmp_path / "data")
    (tmp_path / "data").mkdir()
    return env


def test_entrypoint_refuses_generated_code_when_sandbox_caps_are_denied(tmp_path: Path):
    """A failed capability probe must not exec the server as the service user."""
    env = _install_fake_root_tools(tmp_path, deny_caps=True)
    marker = tmp_path / "strategy-ran"
    completed = subprocess.run(
        [str(ENTRYPOINT), "python3", "-c", f"open({str(marker)!r}, 'w').write('ran-as-service-uid')"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    log = Path(env["SETPRIV_LOG"]).read_text(encoding="utf-8")
    assert completed.returncode != 0
    assert "refusing to start" in completed.stderr
    assert "vibe-sandbox" in completed.stderr
    assert not marker.exists()
    assert "ran-as-service-uid" not in completed.stdout
    assert "ran-as-service-uid" not in log
    assert "--ambient-caps=-all,+setuid,+setgid" in log


def test_entrypoint_execs_only_with_sandbox_caps(tmp_path: Path):
    env = _install_fake_root_tools(tmp_path, deny_caps=False)
    marker = tmp_path / "strategy-ran"
    completed = subprocess.run(
        [str(ENTRYPOINT), "python3", "-c", f"open({str(marker)!r}, 'w').write('ok')"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    log = Path(env["SETPRIV_LOG"]).read_text(encoding="utf-8")
    assert completed.returncode == 0, completed.stderr
    assert marker.read_text(encoding="utf-8") == "ok"
    assert "--ambient-caps=-all,+setuid,+setgid" in log


def _write_inventory(tmp_path: Path, machines: list, volumes: list) -> tuple[Path, Path]:
    machine_path = tmp_path / "machines.json"
    volume_path = tmp_path / "volumes.json"
    machine_path.write_text(json.dumps(machines), encoding="utf-8")
    volume_path.write_text(json.dumps(volumes), encoding="utf-8")
    return machine_path, volume_path


def test_single_machine_check_accepts_one_attached_app_machine(tmp_path: Path):
    machines = [{
        "id": "machine-a",
        "state": "started",
        "config": {
            "metadata": {"fly_process_group": "app"},
            "env": {"OPENROUTER_API_KEY": "should-not-print"},
        },
    }]
    volumes = [{"name": "vibe_data", "attached_machine_id": "machine-a"}]
    ok, reason = _single_machine().evaluate(machines, volumes)
    assert ok is True
    assert "machine-a" in reason
    assert "should-not-print" not in reason
    machine_path, volume_path = _write_inventory(tmp_path, machines, volumes)
    completed = subprocess.run(
        [sys.executable, str(SINGLE_MACHINE), "--machines", str(machine_path), "--volumes", str(volume_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "should-not-print" not in completed.stdout
    assert "should-not-print" not in completed.stderr


def test_single_machine_check_rejects_a_second_app_machine():
    machines = [
        {"id": "machine-a", "state": "started", "config": {"metadata": {"fly_process_group": "app"}}},
        {"id": "machine-b", "state": "created", "config": {"metadata": {"fly_process_group": "app"}}},
    ]
    volumes = [{"name": "vibe_data", "attached_machine_id": "machine-a"}]
    ok, reason = _single_machine().evaluate(machines, volumes)
    assert ok is False
    assert "found 2" in reason
    assert "machine-a" in reason and "machine-b" in reason


def test_single_machine_check_ignores_destroyed_machines_and_rejects_a_loose_volume():
    checker = _single_machine()
    destroyed = [
        {"id": "machine-a", "state": "started", "config": {"metadata": {"fly_process_group": "app"}}},
        {"id": "machine-old", "state": "destroyed", "config": {"metadata": {"fly_process_group": "app"}}},
    ]
    ok, _reason = checker.evaluate(
        destroyed,
        [{"name": "vibe_data", "attached_machine_id": "machine-a"}],
    )
    assert ok is True
    loose, reason = checker.evaluate(
        [{"id": "machine-a", "state": "started", "config": {"metadata": {"fly_process_group": "app"}}}],
        [{"name": "vibe_data", "attached_machine_id": ""}],
    )
    assert loose is False
    assert "not attached" in reason


@pytest.mark.parametrize("name", RESEARCH_PRESETS)
def test_research_swarm_presets_load_and_order_tasks(name: str):
    listed = {item["name"] for item in list_presets()}
    assert name in listed
    plan = inspect_preset(name)
    assert plan["valid"] is True, plan["errors"]
    assert plan["agents"]
    assert plan["tasks"]
    assert plan["layers"]
    flat = [step["task_id"] for layer in plan["layers"] for step in layer]
    assert flat
    # A task cannot appear before a task it depends on.
    position = {task_id: index for index, task_id in enumerate(flat)}
    for task in plan["tasks"]:
        for dep in task["depends_on"]:
            assert position[dep] < position[task["id"]]
