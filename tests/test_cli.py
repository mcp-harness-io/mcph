import re

import pytest
from typer.testing import CliRunner

from mcph import __version__
from mcph.cli import app

runner = CliRunner()

COMMANDS = ["version", "doctor", "proxy", "eval"]

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    """Strip ANSI styling: Typer forces colors when it detects GitHub Actions."""
    return ANSI.sub("", text)


def test_version_prints_installed_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"mcph {__version__}"


@pytest.mark.parametrize("command", ["proxy", "eval"])
def test_placeholder_commands(command):
    result = runner.invoke(app, [command])
    assert result.exit_code == 0
    assert result.output.strip() == f"{command}: coming in v0.1.0"


def test_no_args_shows_help():
    result = runner.invoke(app, [])
    # Typer's no_args_is_help exits with 0 (click < 8.2) or 2 (click >= 8.2)
    assert result.exit_code in (0, 2)
    assert "Usage" in plain(result.output)
    for command in COMMANDS:
        assert command in plain(result.output)


def test_help_lists_all_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "MCP Harness" in plain(result.output)
    assert "--config" in plain(result.output)
    assert "(default: ./mcph.yaml)" in plain(result.output)
    for command in COMMANDS:
        assert command in plain(result.output)


def test_unknown_command_fails():
    result = runner.invoke(app, ["nope"])
    assert result.exit_code == 2
