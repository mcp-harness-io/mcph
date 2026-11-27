"""End-to-end tests: ``mcph doctor`` with rules configured in ``mcph.yaml``."""

import re

import pytest

pytestmark = pytest.mark.e2e

SNAKE_CASE = "core.tool_name_snake_case"
OAUTH_RULES = ("oauth.metadata", "oauth.issuer", "oauth.pkce", "oauth.registration")


def findings(output: str) -> list[str]:
    return re.findall(r"^  (?:ERROR|WARN) +(oauth\.\S+): ", output, re.M)


# --- the rules that look at what a server exposes -------------------------------------------


def test_a_rule_can_be_made_an_error(project):
    project.write_config(
        project.demo_server("orders", tools="get_order,getShipment"),
        rules={SNAKE_CASE: "error"},
    )

    result = project.doctor()

    assert result.exit_code == 1, result.output
    assert (
        "ERROR  core.tool_name_snake_case: "
        "tool 'getShipment' is not snake_case (try 'get_shipment')"
    ) in result.output
    assert "WARN" not in result.output
    assert "1 of 1 server failed." in result.output


def test_a_rule_can_be_turned_off(project):
    project.write_config(project.demo_server("orders", tools="get_order,getShipment"))
    # By hand, because YAML reads an unquoted `off` as false, which mcph accepts.
    with project.config_path.open("a") as config:
        config.write(f"doctor:\n  rules:\n    {SNAKE_CASE}: off\n")

    result = project.doctor("--strict")

    assert result.exit_code == 0, result.output
    assert "WARN" not in result.output
    assert "All servers OK." in result.output


def test_a_rule_that_stays_a_warning_still_fails_a_strict_run(project):
    project.write_config(
        project.demo_server("orders", tools="getShipment"), rules={SNAKE_CASE: "warn"}
    )

    assert project.doctor().exit_code == 0
    assert project.doctor("--strict").exit_code == 1


def test_an_unknown_rule_is_a_config_error_and_no_server_is_started(project, tmp_path):
    pid_file = tmp_path / "server.pid"
    project.write_config(
        project.demo_server("orders", pid_file=pid_file),
        rules={"core.tool_name_snakecase": "off"},
    )

    result = project.doctor()

    assert result.exit_code == 2, result.output
    assert "Error: Invalid config" in result.output
    assert (
        "doctor.rules: unknown rule 'core.tool_name_snakecase' "
        "(did you mean 'core.tool_name_snake_case'?); known rules: core.tool_name_snake_case, "
        "oauth.metadata, oauth.issuer, oauth.pkce, oauth.registration"
    ) in result.output
    assert not pid_file.exists()


def test_a_setting_that_is_not_error_warn_or_off_is_a_config_error(project):
    project.write_config(project.demo_server("orders"), rules={SNAKE_CASE: "fatal"})

    result = project.doctor()

    assert result.exit_code == 2, result.output
    assert f"doctor.rules.{SNAKE_CASE}: Input should be 'error', 'warn' or 'off'" in result.output


# --- the OAuth checks ------------------------------------------------------------------------


def check(project, url, rules):
    project.write_config(project.http_entry("docs", url), rules=rules)
    return project.doctor()


def test_an_oauth_check_can_be_turned_down_to_a_warning(project, fake_oauth_server):
    url = fake_oauth_server(code_challenge_methods_supported=None)

    result = check(project, url, {"oauth.pkce": "warn"})

    assert "WARN   oauth.pkce: the authorization server does not advertise PKCE" in result.output
    assert "ERROR  oauth.pkce" not in result.output
    assert "1 of 1 server failed, 1 warning." in result.output  # it still needs authentication


def test_an_oauth_check_can_be_turned_off(project, fake_oauth_server):
    url = fake_oauth_server(code_challenge_methods_supported=None, registration_endpoint=None)

    result = check(project, url, {"oauth.pkce": "off"})

    assert findings(result.output) == ["oauth.registration"]


def test_the_configured_severity_also_replaces_the_one_for_a_trailing_slash(
    project, fake_oauth_server
):
    url = fake_oauth_server(announced="{base}/")

    default = check(project, url, {})
    configured = check(project, url, {"oauth.issuer": "error"})

    assert "WARN   oauth.issuer: " in default.output
    assert re.search(r"ERROR  oauth\.issuer: .* differ only by a trailing slash", configured.output)


def test_the_authorization_server_is_left_alone_when_every_oauth_check_is_off(
    project, fake_oauth_server
):
    metadata = "/.well-known/oauth-authorization-server"
    url = fake_oauth_server(missing=True)  # that would be an oauth.metadata error

    default = check(project, url, {})
    assert findings(default.output) == ["oauth.metadata"]
    assert metadata in url.hits  # the control: by default doctor does look there

    url.hits.clear()
    result = check(project, url, dict.fromkeys(OAUTH_RULES, "off"))

    assert findings(result.output) == []
    assert metadata not in url.hits
    assert "authentication required: the server answered 401" in result.output
