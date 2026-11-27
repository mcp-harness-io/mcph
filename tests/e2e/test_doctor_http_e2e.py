"""End-to-end tests: ``mcph doctor`` against real local FastMCP servers over HTTP."""

import re

import pytest

pytestmark = pytest.mark.e2e


def test_running_http_server_is_reported_ok(project, start_http_server):
    url = start_http_server(tools="get_order,list_orders,cancel_order")
    project.write_config(project.http_entry("docs", url))

    result = project.doctor()

    assert result.exit_code == 0, result.output
    assert re.search(
        r"docs\n +OK +connected in \d+ ms \(e2e-server 9\.9\.9, MCP \S+\), 3 tools",
        result.output,
    )
    assert "started" not in result.output
    assert "All servers OK." in result.output


def test_rules_apply_to_http_servers_too(project, start_http_server):
    url = start_http_server(tools="get_order,getShipment")
    project.write_config(project.http_entry("docs", url))

    result = project.doctor()

    assert result.exit_code == 0, result.output
    assert "tool 'getShipment' is not snake_case (try 'get_shipment')" in result.output
    assert "All servers OK, 1 warning." in result.output


def test_stdio_and_http_servers_are_checked_together(project, start_http_server):
    url = start_http_server()
    project.write_config(project.demo_server("local"), project.http_entry("remote", url))

    result = project.doctor()

    assert result.exit_code == 0, result.output
    assert re.search(r"local\n +OK +started in \d+ ms", result.output)
    assert re.search(r"remote\n +OK +connected in \d+ ms", result.output)
    assert "All servers OK." in result.output


def test_http_server_is_not_stopped_by_doctor(project, start_http_server):
    url = start_http_server()
    project.write_config(project.http_entry("docs", url))

    assert project.doctor().exit_code == 0
    second = project.doctor()  # still reachable, so mcph left it alone

    assert second.exit_code == 0, second.output


def test_http_server_that_is_not_running_is_reported_with_its_url(
    project, closed_port_url
):
    project.write_config(project.http_entry("docs", closed_port_url))

    result = project.doctor()

    assert result.exit_code == 1, result.output
    assert f"ERROR  failed while connecting to {closed_port_url}:" in result.output
    assert "1 of 1 server failed." in result.output


def test_http_endpoint_that_does_not_exist_is_reported(project, start_http_server):
    url = start_http_server().replace("/mcp", "/nope")
    project.write_config(project.http_entry("docs", url))

    result = project.doctor()

    assert result.exit_code == 1, result.output
    assert f"failed while connecting to {url}: Not Found" in result.output


def test_http_server_that_never_answers_times_out(project, silent_server_url):
    project.write_config(project.http_entry("docs", silent_server_url))

    result = project.doctor("--timeout", "1")

    assert result.exit_code == 1, result.output
    assert (
        f"timed out after 1 s while connecting to {silent_server_url}" in result.output
    )


def test_server_that_requires_authentication_is_explained(project, start_http_server):
    url = start_http_server(auth=1)
    project.write_config(project.http_entry("docs", url))
    port = url.split(":")[2].split("/")[0]

    result = project.doctor()

    assert result.exit_code == 1, result.output
    assert "ERROR  authentication required: the server answered 401" in result.output
    assert (
        'challenge: Bearer resource_metadata="'
        f'http://127.0.0.1:{port}/.well-known/oauth-protected-resource/mcp"'
    ) in result.output
    assert f"authorization server: http://127.0.0.1:{port}/" in result.output
    assert "scopes: api.read, api.write" in result.output
    assert "1 of 1 server failed." in result.output


def test_401_without_oauth_metadata_still_says_authentication_is_required(
    project, plain_401_server_url
):
    project.write_config(project.http_entry("docs", plain_401_server_url))

    result = project.doctor()

    assert result.exit_code == 1, result.output
    assert "ERROR  authentication required: the server answered 401" in result.output
    assert 'challenge: Bearer realm="demo"' in result.output
    assert "no OAuth protected resource metadata found" in result.output


def test_other_servers_are_still_checked_when_one_requires_authentication(
    project, start_http_server
):
    protected = start_http_server(auth=1)
    project.write_config(
        project.http_entry("protected", protected), project.demo_server("local")
    )

    result = project.doctor()

    assert result.exit_code == 1, result.output
    assert re.search(r"protected\n +ERROR +authentication required", result.output)
    assert re.search(r"local\n +OK +started in \d+ ms", result.output)
    assert "1 of 2 servers failed." in result.output

