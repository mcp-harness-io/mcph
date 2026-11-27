"""End-to-end tests: ``mcph doctor`` says what happened to every rule."""

import re

import pytest

pytestmark = pytest.mark.e2e

SNAKE_CASE = "core.tool_name_snake_case"
OAUTH_IDS = "oauth.metadata, oauth.issuer, oauth.pkce, oauth.registration"
NO_CONNECTION = "mcph could not connect to the server"


def lines(output: str) -> list[str]:
    return output.splitlines()


# --- servers that started or connected -------------------------------------------------------


def test_a_rule_that_passed_is_listed_as_ok(project):
    project.write_config(project.demo_server("orders", tools="get_order,list_orders"))

    result = project.doctor()

    assert result.exit_code == 0, result.output
    assert re.search(
        rf"orders\n  OK +started in \d+ ms .*, 2 tools\n  OK     {SNAKE_CASE}\n", result.output
    )
    assert "oauth." not in result.output  # the OAuth checks are about http servers
    assert "All servers OK." in result.output


def test_a_rule_that_found_problems_is_listed_by_its_findings(project):
    project.write_config(project.demo_server("orders", tools="get_order,getShipment"))

    result = project.doctor()

    assert result.exit_code == 0, result.output
    assert f"  WARN   {SNAKE_CASE}: tool 'getShipment' is not snake_case" in result.output
    assert f"  OK     {SNAKE_CASE}" not in lines(result.output)


def test_a_rule_that_is_off_is_listed_as_off(project):
    project.write_config(
        project.demo_server("orders", tools="getShipment"), rules={SNAKE_CASE: "off"}
    )

    result = project.doctor()

    assert result.exit_code == 0, result.output
    assert f"  OFF    {SNAKE_CASE}" in lines(result.output)
    assert "WARN" not in result.output
    assert f"  OK     {SNAKE_CASE}" not in lines(result.output)


def test_an_http_server_that_connected_skips_the_oauth_checks(project, start_http_server):
    project.write_config(project.http_entry("docs", start_http_server(tools="get_order")))

    result = project.doctor()

    assert result.exit_code == 0, result.output
    assert f"  OK     {SNAKE_CASE}" in lines(result.output)
    assert (
        f"  SKIP   {OAUTH_IDS}: not needed, the server accepted the connection"
    ) in lines(result.output)


def test_the_oauth_checks_that_are_off_are_listed_among_the_skipped_ones(
    project, start_http_server
):
    project.write_config(
        project.http_entry("docs", start_http_server()),
        rules={"oauth.pkce": "off", "oauth.registration": "off"},
    )

    result = project.doctor()

    assert result.exit_code == 0, result.output
    assert (
        "  SKIP   oauth.metadata, oauth.issuer: not needed, the server accepted the connection"
    ) in lines(result.output)
    assert "  OFF    oauth.pkce" in lines(result.output)
    assert "  OFF    oauth.registration" in lines(result.output)


# --- servers that did not start or connect ---------------------------------------------------


def test_the_rules_are_skipped_for_a_server_that_crashed(project):
    project.write_config(project.demo_server("crashy", fail_on_start=1))

    result = project.doctor()

    assert result.exit_code == 1, result.output
    assert (
        f"  SKIP   {SNAKE_CASE}: {NO_CONNECTION}"
    ) in lines(result.output)
    assert re.search(r"boom: the demo server refuses to start\n  SKIP ", result.output)


def test_every_rule_is_skipped_for_an_http_server_that_is_not_there(project, closed_port_url):
    project.write_config(project.http_entry("docs", closed_port_url))

    result = project.doctor()

    assert result.exit_code == 1, result.output
    assert f"  SKIP   {SNAKE_CASE}, {OAUTH_IDS}: {NO_CONNECTION}" in lines(result.output)


# --- servers that asked for authentication ---------------------------------------------------


def test_the_oauth_checks_that_passed_are_listed_as_ok(project, start_http_server):
    project.write_config(project.http_entry("docs", start_http_server(auth="oauth"), oauth=True))

    result = project.doctor()

    assert result.exit_code == 1, result.output  # nobody is logged in
    assert "  ERROR  not logged in (run `mcph auth login docs`)" in result.output
    out = lines(result.output)
    assert f"  SKIP   {SNAKE_CASE}: {NO_CONNECTION}" in out
    for rule_id in OAUTH_IDS.split(", "):
        assert f"  OK     {rule_id}" in out


def test_a_finding_replaces_the_ok_line_of_its_oauth_check(project, fake_oauth_server):
    url = fake_oauth_server(code_challenge_methods_supported=None)
    project.write_config(project.http_entry("docs", url))

    result = project.doctor()

    out = lines(result.output)
    assert "  OK     oauth.metadata" in out
    assert "  OK     oauth.issuer" in out
    assert any(line.startswith("  ERROR  oauth.pkce: ") for line in out)
    assert "  OK     oauth.pkce" not in out
    assert "  OK     oauth.registration" in out


def test_the_other_checks_are_skipped_when_the_metadata_cannot_be_found(
    project, fake_oauth_server
):
    project.write_config(project.http_entry("docs", fake_oauth_server(missing=True)))

    result = project.doctor()

    out = lines(result.output)
    assert any(line.startswith("  ERROR  oauth.metadata: cannot find the metadata") for line in out)
    assert (
        "  SKIP   oauth.issuer, oauth.pkce, oauth.registration: "
        "the authorization server's metadata was not found"
    ) in out


def test_the_oauth_checks_are_skipped_when_no_authorization_server_is_announced(
    project, plain_401_server_url
):
    project.write_config(project.http_entry("docs", plain_401_server_url))

    result = project.doctor()

    out = lines(result.output)
    assert f"  SKIP   {SNAKE_CASE}: {NO_CONNECTION}" in out
    assert f"  SKIP   {OAUTH_IDS}: the server announced no authorization server" in out


def test_an_oauth_check_that_is_off_is_listed_as_off(project, fake_oauth_server):
    project.write_config(
        project.http_entry("docs", fake_oauth_server(code_challenge_methods_supported=None)),
        rules={"oauth.pkce": "off"},
    )

    result = project.doctor()

    out = lines(result.output)
    assert "  OFF    oauth.pkce" in out
    assert not any("oauth.pkce:" in line for line in out)


def test_every_oauth_check_can_be_listed_as_off(project, fake_oauth_server):
    project.write_config(
        project.http_entry("docs", fake_oauth_server(missing=True)),
        rules=dict.fromkeys(OAUTH_IDS.split(", "), "off"),
    )

    result = project.doctor()

    out = lines(result.output)
    for rule_id in OAUTH_IDS.split(", "):
        assert f"  OFF    {rule_id}" in out
    assert not any(line.startswith(("  ERROR  oauth.", "  WARN   oauth.")) for line in out)
