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


def test_doctor_is_a_group_and_its_run_command_has_the_options():
    group = runner.invoke(app, ["doctor", "--help"])
    run = runner.invoke(app, ["doctor", "run", "--help"])

    assert group.exit_code == 0 and run.exit_code == 0
    assert re.search(r"\brun\b", plain(group.output))
    assert re.search(r"\brules\b", plain(group.output))
    assert "--timeout" in plain(run.output)
    assert "--strict" in plain(run.output)


def test_a_bare_doctor_fails_so_that_an_old_script_does_not_pass_without_checking():
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 2
    assert "Missing command" in plain(result.output)


@pytest.mark.parametrize("option", [["--strict"], ["--timeout", "5"]])
def test_the_doctor_options_moved_to_run(option):
    result = runner.invoke(app, ["doctor", *option])

    assert result.exit_code == 2
    assert "No such option" in plain(result.output)
