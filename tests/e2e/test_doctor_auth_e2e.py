"""End-to-end tests: ``mcph doctor`` sending bearer tokens to a protected server."""

import re

import pytest

pytestmark = pytest.mark.e2e

SECRET = "e2e-secret-token"  # valid, scope api.read
WRITER = "e2e-writer-token"  # valid, scopes api.read and api.write
VARIABLE = "E2E_TOKEN"


def check(project, url, monkeypatch, token, *args):
    """Run doctor against ``url`` with ``token`` in the environment variable."""
    if token is None:
        monkeypatch.delenv(VARIABLE, raising=False)
    else:
        monkeypatch.setenv(VARIABLE, token)
    project.write_config(project.http_entry("docs", url, token_env=VARIABLE))
    return project.doctor(*args)


def test_a_valid_token_lets_doctor_in(project, start_http_server, monkeypatch):
    url = start_http_server(auth=1)

    result = check(project, url, monkeypatch, SECRET)

    assert result.exit_code == 0, result.output
    assert re.search(r"docs\n +OK +connected in \d+ ms \(e2e-server .*, 2 tools", result.output)
    assert SECRET not in result.output


def test_a_wrong_token_is_reported_as_rejected(project, start_http_server, monkeypatch):
    url = start_http_server(auth=1)

    result = check(project, url, monkeypatch, "not-a-valid-token")

    assert result.exit_code == 1, result.output
    assert f"ERROR  the server rejected the token in ${VARIABLE} (HTTP 401)" in result.output
    assert "error: invalid_token" in result.output
    assert "server says: " in result.output
    assert "authorization server: https://auth.example.test/" in result.output
    assert "not-a-valid-token" not in result.output


def test_a_token_without_the_required_scope_says_which_scope_is_needed(
    project, start_http_server, monkeypatch
):
    url = start_http_server(auth=1, required_scopes="api.write")

    result = check(project, url, monkeypatch, SECRET)

    assert result.exit_code == 1, result.output
    assert f"the server rejected the token in ${VARIABLE}" in result.output
    assert "scope the server asks for: api.write" in result.output
    assert SECRET not in result.output


def test_a_token_with_the_required_scope_is_accepted(
    project, start_http_server, monkeypatch
):
    url = start_http_server(auth=1, required_scopes="api.write")

    result = check(project, url, monkeypatch, WRITER)

    assert result.exit_code == 0, result.output
    assert "All servers OK." in result.output


def test_a_missing_variable_is_reported_and_the_other_servers_are_still_checked(
    project, start_http_server, monkeypatch
):
    url = start_http_server(auth=1)
    monkeypatch.delenv(VARIABLE, raising=False)
    project.write_config(
        project.http_entry("docs", url, token_env=VARIABLE), project.demo_server("local")
    )

    result = project.doctor()

    assert result.exit_code == 1, result.output
    assert f"ERROR  environment variable {VARIABLE} is not set (auth.token_env)" in result.output
    assert re.search(r"local\n +OK +started in", result.output)
    assert "1 of 2 servers failed." in result.output


def test_an_empty_variable_is_reported(project, start_http_server, monkeypatch):
    url = start_http_server(auth=1)

    result = check(project, url, monkeypatch, "  ")

    assert result.exit_code == 1, result.output
    assert f"environment variable {VARIABLE} is empty (auth.token_env)" in result.output


def test_a_token_is_harmless_for_a_server_that_does_not_need_one(
    project, start_http_server, monkeypatch
):
    url = start_http_server()

    result = check(project, url, monkeypatch, SECRET)

    assert result.exit_code == 0, result.output


def test_the_token_never_shows_up_when_the_connection_fails(
    project, closed_port_url, monkeypatch
):
    result = check(project, closed_port_url, monkeypatch, SECRET)

    assert result.exit_code == 1, result.output
    assert "failed while connecting to" in result.output
    assert SECRET not in result.output


def test_without_auth_the_message_suggests_sending_a_token(project, start_http_server):
    url = start_http_server(auth=1)
    project.write_config(project.http_entry("docs", url))

    result = project.doctor()

    assert result.exit_code == 1, result.output
    assert (
        "authentication required: the server answered 401 "
        "(send a bearer token with `auth` in mcph.yaml)"
    ) in result.output
