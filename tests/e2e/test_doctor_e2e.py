"""End-to-end tests: ``mcph doctor`` against real local FastMCP server processes."""

import re
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mcph.cli import app

pytestmark = pytest.mark.e2e


def test_healthy_server_is_reported_ok(project):
    project.write_config(
        project.demo_server("orders", tools="get_order,list_orders,cancel_order")
    )

    result = project.doctor()

    assert result.exit_code == 0, result.output
    assert f"Checking 1 server from {project.config_path}" in result.output
    assert "orders\n" in result.output
    assert re.search(
        r"OK +started in \d+ ms \(e2e-server 9\.9\.9, MCP \S+\), 3 tools", result.output
    )
    assert "All servers OK." in result.output


def test_server_is_stopped_after_the_check(project, tmp_path, wait_until_gone):
    pid_file = tmp_path / "server.pid"
    project.write_config(project.demo_server(pid_file=pid_file))

    result = project.doctor()

    assert result.exit_code == 0, result.output
    assert wait_until_gone(int(pid_file.read_text()))


def test_every_server_is_checked_and_a_failure_does_not_stop_the_rest(project):
    broken = {
        "name": "broken",
        "transport": "stdio",
        "command": "mcph-test-no-such-command",
    }
    project.write_config(broken, project.demo_server("healthy"))

    result = project.doctor()

    assert result.exit_code == 1, result.output
    assert re.search(r"broken\n +ERROR +failed while starting the server", result.output)
    assert "mcph-test-no-such-command" in result.output
    assert re.search(r"healthy\n +OK +started in", result.output)
    assert "1 of 2 servers failed." in result.output


def test_server_that_crashes_on_startup_is_reported_with_its_stderr(project):
    project.write_config(project.demo_server("crashy", fail_on_start=1))

    result = project.doctor()

    assert result.exit_code == 1, result.output
    assert "ERROR  failed while starting the server" in result.output
    assert "server stderr:" in result.output
    assert "boom: the demo server refuses to start" in result.output


def test_server_that_hangs_is_timed_out_and_stopped(project, tmp_path, wait_until_gone):
    pid_file = tmp_path / "server.pid"
    project.write_config(project.demo_server("slow", startup_delay=60, pid_file=pid_file))

    result = project.doctor("--timeout", "1")

    assert result.exit_code == 1, result.output
    assert "timed out after 1 s while starting the server" in result.output
    assert wait_until_gone(int(pid_file.read_text()))


def test_invalid_config_exits_with_2(project):
    project.config_path.write_text("servers: []\n")

    result = project.doctor()

    assert result.exit_code == 2
    assert "Error: Invalid config" in result.output
    assert "servers: List should have at least 1 item" in result.output


def test_missing_config_exits_with_2(project):
    result = project.doctor()  # no mcph.yaml was written

    assert result.exit_code == 2
    assert "Error: Config file not found" in result.output


def test_default_config_is_mcph_yaml_in_the_working_directory(project, monkeypatch):
    project.write_config(project.demo_server("orders"))
    monkeypatch.chdir(project.root)

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert re.search(r"orders\n +OK +started in", result.output)


def test_installed_command_exits_with_the_doctor_status(project):
    """Run the real console script in a subprocess, as a user would."""
    script = Path(sys.executable).parent / "mcph"
    if not script.exists():
        pytest.skip("mcph console script is not installed next to the interpreter")
    broken = {"name": "broken", "transport": "stdio", "command": "mcph-test-no-such-command"}
    project.write_config(project.demo_server("orders"), broken)

    completed = subprocess.run(
        [str(script), "-c", str(project.config_path), "doctor"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "1 of 2 servers failed." in completed.stdout
