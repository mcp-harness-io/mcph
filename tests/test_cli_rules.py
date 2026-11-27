import re

import pytest
from typer.testing import CliRunner

from mcph.cli import app
from mcph.rules.builtin import known_rules

runner = CliRunner()

ANSI = re.compile(r"\x1b\[[0-9;]*m")
SERVERS = "servers:\n  - {name: orders, transport: stdio, command: uv}\n"
DEFAULTS = {rule_id: rule.severity.value for rule_id, rule in known_rules().items()}


def plain(text: str) -> str:
    return ANSI.sub("", text)


@pytest.fixture
def config_path(tmp_path):
    return tmp_path / "mcph.yaml"


def settings_shown(output: str) -> dict[str, str]:
    """The ``rule id -> setting`` pairs of a listing, such as ``error (default: warn)``."""
    shown = {}
    for line in output.splitlines()[2:]:
        rule_id, rest = line.split(None, 1)
        shown[rule_id] = re.match(r"\S+(?: \(default: \S+\))?", rest).group(0)
    return shown


def test_a_config_shows_the_setting_and_the_default_of_a_changed_rule(config_path):
    config_path.write_text(
        SERVERS
        + "doctor:\n  rules:\n    core.tool_name_snake_case: error\n    oauth.pkce: off\n"
    )

    result = runner.invoke(app, ["-c", str(config_path), "doctor", "rules"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[:2] == [f"Rules (settings from {config_path})", ""]
    assert settings_shown(result.output) == {
        **DEFAULTS,
        "core.tool_name_snake_case": "error (default: warn)",
        "oauth.pkce": "off (default: error)",
    }


def test_every_rule_is_listed_with_its_description(config_path):
    config_path.write_text(SERVERS)

    result = runner.invoke(app, ["-c", str(config_path), "doctor", "rules"])

    lines = result.output.splitlines()[2:]
    assert [line.split()[0] for line in lines] == list(known_rules())
    for line, rule in zip(lines, known_rules().values()):
        assert line.endswith(rule.description)


def test_a_config_without_settings_lists_the_defaults(config_path):
    config_path.write_text(SERVERS)

    result = runner.invoke(app, ["-c", str(config_path), "doctor", "rules"])

    assert result.exit_code == 0, result.output
    assert result.output.startswith(f"Rules (settings from {config_path})")
    assert settings_shown(result.output) == DEFAULTS
    assert "default:" not in result.output


def test_without_a_config_in_the_current_directory_the_defaults_are_listed(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["doctor", "rules"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == (
        "Rules (default settings: no mcph.yaml in the current directory)"
    )
    assert settings_shown(result.output) == DEFAULTS


def test_the_config_in_the_current_directory_is_used_by_default(tmp_path, monkeypatch):
    (tmp_path / "mcph.yaml").write_text(SERVERS + "doctor:\n  rules:\n    oauth.pkce: warn\n")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["doctor", "rules"])

    assert result.exit_code == 0, result.output
    assert re.match(r"Rules \(settings from .*mcph\.yaml\)", result.output)
    assert settings_shown(result.output)["oauth.pkce"] == "warn (default: error)"


def test_a_config_path_that_does_not_exist_is_an_error_not_the_defaults(tmp_path):
    result = runner.invoke(app, ["-c", str(tmp_path / "nope.yaml"), "doctor", "rules"])

    assert result.exit_code == 2
    assert "Error: Config file not found" in plain(result.output)
    assert "Rules (" not in result.output


def test_an_invalid_config_is_an_error(config_path):
    config_path.write_text(SERVERS + "doctor:\n  rules:\n    core.tool_name_snakecase: off\n")

    result = runner.invoke(app, ["-c", str(config_path), "doctor", "rules"])

    assert result.exit_code == 2
    assert "unknown rule 'core.tool_name_snakecase'" in plain(result.output)


def test_the_listing_starts_no_server(config_path):
    broken = "  - {name: broken, transport: stdio, command: mcph-test-no-such-command}\n"
    config_path.write_text(SERVERS + broken)

    result = runner.invoke(app, ["-c", str(config_path), "doctor", "rules"])

    assert result.exit_code == 0, result.output
    assert "broken" not in result.output


def test_a_directory_named_like_the_config_is_an_error_not_the_defaults(tmp_path, monkeypatch):
    (tmp_path / "mcph.yaml").mkdir()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["doctor", "rules"])

    assert result.exit_code == 2
    assert "Cannot read config file" in plain(result.output)
