import re
import textwrap

import pytest
from typer.testing import CliRunner

from mcph.cli import app

runner = CliRunner()

ANSI = re.compile(r"\x1b\[[0-9;]*m")
SERVERS = "servers:\n  - {name: orders, transport: stdio, command: uv}\n"
CORE = "extends:\n  - builtin:core\n"
CORE_IDS = [
    "core.tool_name_snake_case",
    "oauth.metadata",
    "oauth.issuer",
    "oauth.pkce",
    "oauth.registration",
]
DEFAULTS = {
    "core.tool_name_snake_case": "warn",
    "oauth.metadata": "error",
    "oauth.issuer": "error",
    "oauth.pkce": "error",
    "oauth.registration": "warn",
}
NO_RULES = (
    "No rules are loaded: add a pack to `extends` in mcph.yaml "
    "(`extends: [builtin:core]` has the rules that ship with mcph)."
)


def plain(text: str) -> str:
    return ANSI.sub("", text)


@pytest.fixture
def config_path(tmp_path):
    return tmp_path / "mcph.yaml"


def settings_shown(output: str) -> dict[str, str]:
    """The ``rule id -> setting`` pairs of a listing, such as ``error (default: warn)``."""
    shown = {}
    for line in output.splitlines():
        if line.startswith("  ") and not line.startswith("  ("):
            rule_id, rest = line.split(None, 1)
            shown[rule_id] = re.match(r"\S+(?: \(default: \S+\))?", rest).group(0)
    return shown


def groups_shown(output: str) -> list[str]:
    return [line for line in output.splitlines()[2:] if line and not line.startswith(" ")]


def test_the_core_pack_lists_its_rules_with_the_setting_and_the_default_of_a_changed_one(
    config_path,
):
    config_path.write_text(
        SERVERS
        + CORE
        + "doctor:\n  rules:\n    core.tool_name_snake_case: error\n    oauth.pkce: off\n"
    )

    result = runner.invoke(app, ["-c", str(config_path), "doctor", "rules"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[:3] == [
        f"Rules (settings from {config_path})",
        "",
        "core (builtin:core)",
    ]
    assert settings_shown(result.output) == {
        **DEFAULTS,
        "core.tool_name_snake_case": "error (default: warn)",
        "oauth.pkce": "off (default: error)",
    }


def test_every_rule_is_listed_with_its_description_in_the_order_of_the_pack(config_path):
    config_path.write_text(SERVERS + CORE)

    result = runner.invoke(app, ["-c", str(config_path), "doctor", "rules"])

    lines = [line for line in result.output.splitlines() if line.startswith("  ")]
    assert [line.split()[0] for line in lines] == CORE_IDS
    assert lines[0].endswith("Tool names are snake_case.")
    assert lines[4].endswith("Dynamic client registration is offered.")


def test_a_local_pack_is_listed_after_the_core_pack_and_can_be_configured(
    config_path, tmp_path
):
    pack = tmp_path / "packs" / "acme"
    (pack / "rules").mkdir(parents=True)
    (pack / "mcph-pack.yaml").write_text('name: acme\nversion: "1"\nrules:\n  - rules/a.py\n')
    (pack / "rules" / "a.py").write_text(
        textwrap.dedent(
            """
            from mcph.extensions import doctor_rule

            @doctor_rule(id="acme.no_delete", description="No tool deletes data.", severity="error")
            def no_delete(context):
                return []
            """
        )
    )
    config_path.write_text(
        SERVERS
        + "extends:\n  - builtin:core\n  - ./packs/acme\n"
        + "doctor:\n  rules:\n    acme.no_delete: warn\n"
    )

    result = runner.invoke(app, ["-c", str(config_path), "doctor", "rules"])

    assert result.exit_code == 0, result.output
    assert groups_shown(result.output) == ["core (builtin:core)", "acme (./packs/acme)"]
    shown = settings_shown(result.output)
    assert list(shown) == [*CORE_IDS, "acme.no_delete"]
    assert shown["acme.no_delete"] == "warn (default: error)"


def test_a_config_without_extends_has_no_rule_and_says_how_to_get_some(config_path):
    config_path.write_text(SERVERS)

    result = runner.invoke(app, ["-c", str(config_path), "doctor", "rules"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [f"Rules (settings from {config_path})", "", NO_RULES]


def test_without_a_config_in_the_current_directory_there_is_no_rule_either(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["doctor", "rules"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "Rules (no mcph.yaml in the current directory)",
        "",
        NO_RULES,
    ]


def test_the_config_in_the_current_directory_is_used_by_default(tmp_path, monkeypatch):
    (tmp_path / "mcph.yaml").write_text(SERVERS + CORE + "doctor:\n  rules:\n    oauth.pkce: warn\n")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["doctor", "rules"])

    assert result.exit_code == 0, result.output
    assert re.match(r"Rules \(settings from .*mcph\.yaml\)", result.output)
    assert settings_shown(result.output)["oauth.pkce"] == "warn (default: error)"


def test_a_config_path_that_does_not_exist_is_an_error(tmp_path):
    result = runner.invoke(app, ["-c", str(tmp_path / "nope.yaml"), "doctor", "rules"])

    assert result.exit_code == 2
    assert "Error: Config file not found" in plain(result.output)
    assert "Rules (" not in result.output


def test_an_invalid_config_is_an_error(config_path):
    config_path.write_text(SERVERS + "doctor:\n  rules:\n    oauth.pkce: fatal\n")

    result = runner.invoke(app, ["-c", str(config_path), "doctor", "rules"])

    assert result.exit_code == 2
    assert "Input should be 'error', 'warn' or 'off'" in plain(result.output)


def test_a_rule_that_no_loaded_pack_defines_is_an_error(config_path):
    config_path.write_text(SERVERS + CORE + "doctor:\n  rules:\n    core.tool_name_snakecase: off\n")

    result = runner.invoke(app, ["-c", str(config_path), "doctor", "rules"])

    assert result.exit_code == 2
    assert "unknown rule 'core.tool_name_snakecase' (did you mean 'core.tool_name_snake_case'?)" in (
        plain(result.output)
    )


def test_a_pack_that_cannot_be_found_is_an_error(config_path):
    config_path.write_text(SERVERS + "extends:\n  - ./packs/missing\n")

    result = runner.invoke(app, ["-c", str(config_path), "doctor", "rules"])

    assert result.exit_code == 2
    assert "Error: pack directory not found" in plain(result.output)
    assert "'./packs/missing'" in plain(result.output)


def test_a_directory_named_like_the_config_is_an_error_not_an_empty_listing(
    tmp_path, monkeypatch
):
    (tmp_path / "mcph.yaml").mkdir()
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["doctor", "rules"])

    assert result.exit_code == 2
    assert "Cannot read config file" in plain(result.output)


def test_the_listing_starts_no_server(config_path):
    broken = "  - {name: broken, transport: stdio, command: mcph-test-no-such-command}\n"
    config_path.write_text(SERVERS + broken + CORE)

    result = runner.invoke(app, ["-c", str(config_path), "doctor", "rules"])

    assert result.exit_code == 0, result.output
    assert "broken" not in result.output
