"""End-to-end tests: ``mcph doctor rules`` with a real config file."""

import re

import pytest

pytestmark = pytest.mark.e2e


def test_the_rules_are_listed_with_the_settings_of_the_config(project):
    project.write_config(
        project.demo_server("orders"),
        rules={"core.tool_name_snake_case": "error", "oauth.registration": "off"},
    )

    result = project.mcph("doctor", "rules")

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[:2] == [f"Rules (settings from {project.config_path})", ""]
    assert re.fullmatch(
        r"  core\.tool_name_snake_case  error \(default: warn\)  Tool names are snake_case\.",
        lines[2],
    )
    assert re.fullmatch(r"  oauth\.metadata +error +The authorization server's .*", lines[3])
    assert re.fullmatch(
        r"  oauth\.registration +off \(default: warn\) +Dynamic client .*", lines[6]
    )
    assert len(lines) == 7


def test_listing_the_rules_starts_no_server(project, tmp_path):
    pid_file = tmp_path / "server.pid"
    project.write_config(project.demo_server("orders", pid_file=pid_file))

    result = project.mcph("doctor", "rules")

    assert result.exit_code == 0, result.output
    assert not pid_file.exists()  # the demo server writes it when it starts


def test_the_checks_use_the_settings_that_the_listing_shows(project):
    project.write_config(
        project.demo_server("orders", tools="getShipment"),
        rules={"core.tool_name_snake_case": "error"},
    )

    listing = project.mcph("doctor", "rules")
    checks = project.doctor()

    assert "core.tool_name_snake_case  error (default: warn)" in listing.output
    assert checks.exit_code == 1, checks.output  # the rule is an error, so the run fails
    assert "  ERROR  core.tool_name_snake_case: tool 'getShipment'" in checks.output
