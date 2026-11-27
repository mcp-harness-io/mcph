import re
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

import mcph.extensions
from mcph.cli import app

runner = CliRunner()

ANSI = re.compile(r"\x1b\[[0-9;]*m")
SERVERS = "servers:\n  - {name: orders, transport: stdio, command: uv}\n"
NO_PACKS = (
    "No packs are loaded: add a pack to `extends` in mcph.yaml "
    "(`extends: [builtin:core]` has the rules that ship with mcph)."
)
RULE_FILE = textwrap.dedent(
    """
    from mcph.extensions import doctor_rule


    @doctor_rule(id="acme.no_delete", description="No tool deletes data.", severity="error")
    def no_delete(context):
        return []
    """
)


def plain(text: str) -> str:
    return ANSI.sub("", text)


@pytest.fixture
def config_path(tmp_path):
    return tmp_path / "mcph.yaml"


@pytest.fixture
def acme(tmp_path):
    """A valid pack directory called acme with one rule."""
    pack = tmp_path / "packs" / "acme"
    (pack / "rules").mkdir(parents=True)
    (pack / "mcph-pack.yaml").write_text(
        'name: acme\nversion: "1.2.0"\ndescription: Our checks.\nrules:\n  - rules/no_delete.py\n'
    )
    (pack / "rules" / "no_delete.py").write_text(RULE_FILE)
    return pack


# --- pack list --------------------------------------------------------------------------------


def test_the_loaded_packs_are_listed_with_their_rules(config_path, acme):
    config_path.write_text(SERVERS + "extends:\n  - builtin:core\n  - ./packs/acme\n")

    result = runner.invoke(app, ["-c", str(config_path), "pack", "list"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        f"Packs (from {config_path})",
        "",
        "core 1.0.0 (builtin:core)",
        "  The rules that ship with mcph.",
        "  core.tool_name_snake_case",
        "  oauth.metadata",
        "  oauth.issuer",
        "  oauth.pkce",
        "  oauth.registration",
        "",
        "acme 1.2.0 (./packs/acme)",
        "  Our checks.",
        "  acme.no_delete",
    ]


def test_a_config_without_extends_has_no_pack_and_says_how_to_get_one(config_path):
    config_path.write_text(SERVERS)

    result = runner.invoke(app, ["-c", str(config_path), "pack", "list"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [f"Packs (from {config_path})", "", NO_PACKS]


def test_without_a_config_in_the_current_directory_no_pack_is_loaded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["pack", "list"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "Packs (no mcph.yaml in the current directory)",
        "",
        NO_PACKS,
    ]


def test_the_config_in_the_current_directory_is_used_by_default(tmp_path, monkeypatch):
    (tmp_path / "mcph.yaml").write_text(SERVERS + "extends:\n  - builtin:core\n")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["pack", "list"])

    assert result.exit_code == 0, result.output
    assert "core 1.0.0 (builtin:core)" in result.output


def test_listing_does_not_mind_a_setting_for_a_rule_no_pack_defines(config_path):
    config_path.write_text(
        SERVERS + "extends:\n  - builtin:core\ndoctor:\n  rules:\n    zzz: warn\n"
    )

    result = runner.invoke(app, ["-c", str(config_path), "pack", "list"])

    assert result.exit_code == 0, result.output  # `doctor run` would reject the setting


def test_a_pack_that_cannot_be_found_is_an_error(config_path):
    config_path.write_text(SERVERS + "extends:\n  - ./packs/missing\n")

    result = runner.invoke(app, ["-c", str(config_path), "pack", "list"])

    assert result.exit_code == 2
    assert "Error: pack directory not found" in plain(result.output)


def test_a_config_path_that_does_not_exist_is_an_error(tmp_path):
    result = runner.invoke(app, ["-c", str(tmp_path / "nope.yaml"), "pack", "list"])

    assert result.exit_code == 2
    assert "Error: Config file not found" in plain(result.output)


def test_listing_starts_no_server(config_path):
    broken = "  - {name: broken, transport: stdio, command: mcph-test-no-such-command}\n"
    config_path.write_text(SERVERS + broken + "extends:\n  - builtin:core\n")

    result = runner.invoke(app, ["-c", str(config_path), "pack", "list"])

    assert result.exit_code == 0, result.output
    assert "broken" not in result.output


# --- pack verify ------------------------------------------------------------------------------


def test_a_good_pack_is_valid(acme):
    result = runner.invoke(app, ["pack", "verify", str(acme)])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "acme 1.2.0: valid, 1 rule",
        "  acme.no_delete  error  No tool deletes data.",
    ]


def test_the_pack_in_the_current_directory_is_verified_by_default(acme, monkeypatch):
    monkeypatch.chdir(acme)

    result = runner.invoke(app, ["pack", "verify"])

    assert result.exit_code == 0, result.output
    assert result.output.startswith("acme 1.2.0: valid")


def test_every_problem_of_a_broken_pack_is_listed_and_the_exit_code_is_1(acme):
    (acme / "mcph-pack.yaml").write_text(
        'name: acme\nversion: "1.2.0"\nrules:\n  - rules/missing.py\n  - rules/no_delete.py\n'
        "  - rules/broken.py\n"
    )
    (acme / "rules" / "broken.py").write_text("raise RuntimeError('boom')\n")

    result = runner.invoke(app, ["pack", "verify", str(acme)])

    assert result.exit_code == 1
    lines = result.output.splitlines()
    assert lines[0] == f"{acme}: 2 problems"
    assert lines[1] == (
        "  pack 'acme': rules/missing.py, listed under rules in mcph-pack.yaml, does not exist"
    )
    assert lines[2] == "  pack 'acme': cannot import broken.py: RuntimeError: boom"
    assert len(lines) == 3


def test_an_invalid_manifest_is_a_problem(acme):
    (acme / "mcph-pack.yaml").write_text("name: acme\n")

    result = runner.invoke(app, ["pack", "verify", str(acme)])

    assert result.exit_code == 1
    assert result.output.splitlines()[0] == f"{acme}: 1 problem"
    assert "    version: Field required" in result.output


def test_a_directory_that_is_not_a_pack_is_a_problem(tmp_path):
    result = runner.invoke(app, ["pack", "verify", str(tmp_path)])

    assert result.exit_code == 1
    assert "is not a pack: it has no mcph-pack.yaml" in result.output


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_a_path_that_is_not_a_directory_is_a_usage_error(tmp_path, kind):
    target = tmp_path / "nope"
    if kind == "file":
        target.write_text("x")

    result = runner.invoke(app, ["pack", "verify", str(target)])

    assert result.exit_code == 2
    assert "Invalid value" in plain(result.output)


def test_the_core_pack_that_ships_with_mcph_is_valid():
    core = mcph.extensions.resolve_pack("builtin:core", Path.cwd()).directory

    result = runner.invoke(app, ["pack", "verify", str(core)])

    assert result.exit_code == 0, result.output
    assert result.output.startswith("core 1.0.0: valid, 5 rules")


def test_pack_without_a_command_shows_the_commands():
    result = runner.invoke(app, ["pack"])

    assert result.exit_code in (0, 2)  # no_args_is_help: 0 before click 8.2, 2 since
    assert "list" in plain(result.output) and "verify" in plain(result.output)


def test_the_pack_commands_are_in_the_help():
    result = runner.invoke(app, ["--help"])

    assert "pack" in plain(result.output)
