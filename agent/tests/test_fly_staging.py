"""Fly.io staging profile stays single-instance, secret-free, and paper-only."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest

from src.swarm.presets import inspect_preset, list_presets

ROOT = Path(__file__).resolve().parents[2]
FLY_TOML = ROOT / "fly.toml"
DOCKERFILE = ROOT / "Dockerfile"
ENTRYPOINT = ROOT / "deploy" / "docker-entrypoint.sh"

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


def test_fly_dockerfile_target_does_not_replace_the_default_image():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "FROM runtime AS fly" in text
    assert text.strip().endswith("FROM runtime")
    assert "USER vibe" in text
    assert "vibe-sandbox" in text
    assert "/usr/local/bin/vibe-trading-entrypoint" in text


def test_entrypoint_refuses_world_writable_data_and_keeps_sandbox_caps():
    script = ENTRYPOINT.read_text(encoding="utf-8")
    assert re.search(r"^\s*chmod\b", script, re.MULTILINE) is None
    assert "0777" not in script
    assert "777" not in script
    assert "setpriv" in script
    assert "+setuid,+setgid" in script
    assert "vibe-sandbox" in script
    assert "refusing to start" in script
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
