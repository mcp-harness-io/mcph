"""End-to-end tests: ``mcph doctor`` with packs of rules, from the config to the report."""

import re
import textwrap

import pytest

pytestmark = pytest.mark.e2e

SNAKE_CASE = "core.tool_name_snake_case"

NO_DELETE = """
from mcph.extensions import doctor_rule


@doctor_rule(id="acme.no_delete", description="No tool deletes data.", severity="warn")
def no_delete(context):
    return [
        f"{tool.name} deletes data"
        for tool in context.snapshot.tools
        if tool.name.startswith("delete")
    ]
"""


@pytest.fixture
def write_pack(project):
    """Write a pack under the project, next to mcph.yaml, and return its directory."""

    def write(name: str = "acme", rule_source: str = NO_DELETE, version: str = "1.0.0"):
        pack = project.root / "packs" / name
        (pack / "rules").mkdir(parents=True, exist_ok=True)
        (pack / "rules" / "rules.py").write_text(textwrap.dedent(rule_source))
        (pack / "mcph-pack.yaml").write_text(
            f'name: {name}\nversion: "{version}"\nrules:\n  - rules/rules.py\n'
        )
        return pack

    return write


# --- without any pack there is no rule --------------------------------------------------------


def test_without_extends_no_rule_runs_and_the_report_warns(project):
    project.write_config(project.demo_server("orders", tools="getShipment"), extends=None)

    result = project.doctor()

    assert result.exit_code == 0, result.output
    assert re.search(r"orders\n +OK +started in \d+ ms .*, 1 tool\n\nWarning: no rules", result.output)
    assert "core.tool_name_snake_case" not in result.output
    assert (
        "Warning: no rules are loaded, so the servers were only reached, not checked: "
        "add a pack to `extends` in mcph.yaml (`extends: [builtin:core]` has the rules "
        "that ship with mcph)."
    ) in result.output
    assert "All servers OK, 1 warning." in result.output


def test_without_any_rule_a_strict_run_fails(project):
    project.write_config(project.demo_server("orders"), extends=None)

    assert project.doctor().exit_code == 0
    assert project.doctor("--strict").exit_code == 1


def test_the_core_pack_brings_back_the_rules_that_ship_with_mcph(project):
    project.write_config(
        project.demo_server("orders", tools="getShipment"), extends=["builtin:core"]
    )

    result = project.doctor()

    assert result.exit_code == 0, result.output
    assert f"  WARN   {SNAKE_CASE}: tool 'getShipment' is not snake_case" in result.output
    assert "Warning: no rules" not in result.output


# --- a pack of your own -----------------------------------------------------------------------


def test_a_local_pack_adds_its_rules_to_the_report(project, write_pack):
    write_pack()
    project.write_config(
        project.demo_server("orders", tools="get_order,delete_order"),
        extends=["builtin:core", "./packs/acme"],
    )

    result = project.doctor()

    assert result.exit_code == 0, result.output
    assert f"  OK     {SNAKE_CASE}" in result.output.splitlines()
    assert "  WARN   acme.no_delete: delete_order deletes data" in result.output
    assert "All servers OK, 1 warning." in result.output


def test_a_local_pack_works_without_the_core_pack(project, write_pack):
    write_pack()
    project.write_config(
        project.demo_server("orders", tools="getShipment,delete_order"),
        extends=["./packs/acme"],
    )

    result = project.doctor()

    assert "  WARN   acme.no_delete: delete_order deletes data" in result.output
    assert "core.tool_name_snake_case" not in result.output  # nothing asked for the core pack
    assert "Warning: no rules" not in result.output


def test_the_settings_of_the_config_apply_to_the_rules_of_a_pack(project, write_pack):
    write_pack()
    project.write_config(
        project.demo_server("orders", tools="delete_order"),
        extends=["./packs/acme"],
        rules={"acme.no_delete": "error"},
    )

    result = project.doctor()

    assert result.exit_code == 1, result.output
    assert "  ERROR  acme.no_delete: delete_order deletes data" in result.output


def test_a_rule_of_a_pack_that_raises_is_an_error_and_the_other_rules_still_run(
    project, write_pack
):
    write_pack(
        rule_source="""
        from mcph.extensions import doctor_rule

        @doctor_rule(id="acme.broken", description="Always fails.")
        def broken(context):
            raise RuntimeError("boom")
        """
    )
    project.write_config(
        project.demo_server("orders", tools="get_order"),
        extends=["builtin:core", "./packs/acme"],
    )

    result = project.doctor()

    assert result.exit_code == 1, result.output
    assert "  ERROR  acme.broken: the rule failed: RuntimeError: boom" in result.output
    assert f"  OK     {SNAKE_CASE}" in result.output.splitlines()


def test_the_listing_shows_the_packs_in_the_order_of_extends(project, write_pack):
    write_pack()
    project.write_config(
        project.demo_server("orders"),
        extends=["./packs/acme", "builtin:core"],
        rules={"acme.no_delete": "error"},
    )

    result = project.mcph("doctor", "rules")

    assert result.exit_code == 0, result.output
    groups = [line for line in result.output.splitlines()[2:] if line and line[0] != " "]
    assert groups == ["acme (./packs/acme)", "core (builtin:core)"]
    assert "  acme.no_delete" in result.output and "error (default: warn)" in result.output


# --- what goes wrong --------------------------------------------------------------------------


def test_a_pack_directory_that_does_not_exist_is_a_config_error_and_no_server_starts(
    project, tmp_path
):
    pid_file = tmp_path / "server.pid"
    project.write_config(
        project.demo_server("orders", pid_file=pid_file), extends=["./packs/missing"]
    )

    result = project.doctor()

    assert result.exit_code == 2, result.output
    assert "Error: pack directory not found: " in result.output
    assert "(from './packs/missing')" in result.output
    assert not pid_file.exists()


def test_a_pack_whose_rule_file_cannot_be_imported_is_a_config_error(project, write_pack):
    write_pack(rule_source="raise RuntimeError('cannot start')\n")
    project.write_config(project.demo_server("orders"), extends=["./packs/acme"])

    result = project.doctor()

    assert result.exit_code == 2, result.output
    assert "Error: pack 'acme': cannot import rules.py: RuntimeError: cannot start" in result.output


def test_a_setting_for_a_rule_that_no_loaded_pack_defines_is_a_config_error(project):
    project.write_config(
        project.demo_server("orders"), extends=None, rules={SNAKE_CASE: "off"}
    )

    result = project.doctor()

    assert result.exit_code == 2, result.output
    assert "doctor.rules: unknown rule 'core.tool_name_snake_case': no rule is loaded; add a pack" in (
        result.output
    )
