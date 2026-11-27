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
