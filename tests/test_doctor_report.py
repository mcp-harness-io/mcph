import asyncio
from pathlib import Path

from mcph import doctor
from mcph.auth_probe import AuthInfo
from mcph.config import Config, StdioServerConfig
from mcph.doctor import DoctorReport, ServerReport, format_report, run_doctor
from mcph.mcp_client import ServerError, ServerSnapshot, ToolInfo
from mcph.rules import Finding, Rule, Severity


def snapshot(
    *,
    name="orders-server",
    version="1.2.3",
    protocol="2026-07-28",
    seconds=0.3124,
    tools=("get_order", "list_orders"),
    transport="stdio",
) -> ServerSnapshot:
    return ServerSnapshot(
        server_name=name,
        server_version=version,
        protocol_version=protocol,
        startup_seconds=seconds,
        tools=tuple(ToolInfo(tool) for tool in tools),
        transport=transport,
    )


def test_report_for_a_healthy_and_a_failed_server():
    report = DoctorReport(
        (
            ServerReport("orders", snapshot=snapshot()),
            ServerReport(
                "legacy",
                error=ServerError(
                    "failed while starting the server: Connection closed",
                    "Traceback (most recent call last):\nboom",
                ),
            ),
        )
    )

    assert format_report(report, config_path=Path("/p/mcph.yaml")) == "\n".join(
        [
            "Checking 2 servers from /p/mcph.yaml",
            "",
            "orders",
            "  OK     started in 312 ms (orders-server 1.2.3, MCP 2026-07-28), 2 tools",
            "legacy",
            "  ERROR  failed while starting the server: Connection closed",
            "         server stderr:",
            "           Traceback (most recent call last):",
            "           boom",
            "",
            "1 of 2 servers failed.",
        ]
    )


def test_report_for_a_single_healthy_server_without_a_config_path():
    report = DoctorReport((ServerReport("orders", snapshot=snapshot(tools=("a",))),))

    assert format_report(report) == "\n".join(
        [
            "Checking 1 server",
            "",
            "orders",
            "  OK     started in 312 ms (orders-server 1.2.3, MCP 2026-07-28), 1 tool",
            "",
            "All servers OK.",
        ]
    )


def test_report_leaves_out_details_the_server_did_not_provide():
    bare = snapshot(name=None, version=None, protocol=None, seconds=0.005, tools=())
    report = DoctorReport((ServerReport("orders", snapshot=bare),))

    assert "  OK     started in 5 ms, 0 tools" in format_report(report)


def test_report_shows_only_the_protocol_when_the_server_gave_no_name():
    report = DoctorReport(
        (ServerReport("orders", snapshot=snapshot(name=None, version=None)),)
    )

    assert "started in 312 ms (MCP 2026-07-28)" in format_report(report)


def test_report_for_an_error_without_stderr_has_no_stderr_section():
    report = DoctorReport(
        (ServerReport("orders", error=ServerError("timed out after 1 s while starting the server")),)
    )

    text = format_report(report)

    assert "  ERROR  timed out after 1 s while starting the server" in text
    assert "server stderr" not in text
    assert "1 of 1 server failed." in text


def test_doctor_report_is_ok_only_if_every_server_is_ok():
    healthy = ServerReport("a", snapshot=snapshot())
    broken = ServerReport("b", error=ServerError("nope"))

    assert DoctorReport((healthy,)).ok
    assert not DoctorReport((healthy, broken)).ok


def test_run_doctor_checks_every_server_in_order_and_survives_failures(monkeypatch):
    calls = []

    async def fake_snapshot(server, *, cwd, timeout):
        calls.append((server.name, cwd, timeout))
        if server.name == "broken":
            raise ServerError("boom")
        return snapshot()

    monkeypatch.setattr(doctor, "snapshot_server", fake_snapshot)
    config = Config(
        servers=[
            StdioServerConfig(name=name, transport="stdio", command="x")
            for name in ("first", "broken", "last")
        ]
    )

    report = asyncio.run(run_doctor(config, timeout=7))

    assert [server.name for server in report.servers] == ["first", "broken", "last"]
    assert [server.ok for server in report.servers] == [True, False, True]
    assert report.servers[1].error.message == "boom"
    assert calls == [(name, config.base_dir, 7) for name in ("first", "broken", "last")]


WARNING = Finding(
    "core.tool_name_snake_case",
    Severity.WARN,
    "tool 'getOrder' is not snake_case (try 'get_order')",
)


def test_findings_are_listed_under_their_server():
    report = DoctorReport(
        (ServerReport("orders", snapshot=snapshot(), findings=(WARNING,)),)
    )

    assert format_report(report) == "\n".join(
        [
            "Checking 1 server",
            "",
            "orders",
            "  OK     started in 312 ms (orders-server 1.2.3, MCP 2026-07-28), 2 tools",
            "  WARN   core.tool_name_snake_case: tool 'getOrder' is not snake_case"
            " (try 'get_order')",
            "",
            "All servers OK, 1 warning.",
        ]
    )


def test_summary_counts_warnings_and_failed_servers_together():
    two_warnings = (WARNING, WARNING)
    report = DoctorReport(
        (
            ServerReport("a", snapshot=snapshot(), findings=two_warnings),
            ServerReport("b", error=ServerError("boom")),
        )
    )

    text = format_report(report)

    assert text.endswith("1 of 2 servers failed, 2 warnings.")


def test_error_findings_fail_the_server_and_are_labelled_error():
    problem = Finding("acme.rule", Severity.ERROR, "something is wrong")
    report = DoctorReport(
        (ServerReport("orders", snapshot=snapshot(), findings=(problem,)),)
    )

    text = format_report(report)

    assert not report.ok
    assert "  ERROR  acme.rule: something is wrong" in text
    assert text.endswith("1 of 1 server failed.")


def test_passed_treats_warnings_as_failures_only_when_strict():
    clean = DoctorReport((ServerReport("a", snapshot=snapshot()),))
    warned = DoctorReport((ServerReport("a", snapshot=snapshot(), findings=(WARNING,)),))
    broken = DoctorReport((ServerReport("a", error=ServerError("boom")),))

    assert clean.passed() and clean.passed(strict=True)
    assert warned.warning_count == 1
    assert warned.passed() and not warned.passed(strict=True)
    assert not broken.passed() and not broken.passed(strict=True)


def test_run_doctor_applies_the_builtin_rules_by_default(monkeypatch):
    async def fake_snapshot(server, *, cwd, timeout):
        return snapshot(tools=("get_order", "getShipment"))

    monkeypatch.setattr(doctor, "snapshot_server", fake_snapshot)
    config = Config(servers=[StdioServerConfig(name="orders", transport="stdio", command="x")])

    report = asyncio.run(run_doctor(config))

    [finding] = report.servers[0].findings
    assert finding.rule_id == "core.tool_name_snake_case"
    assert "'getShipment'" in finding.message


def test_run_doctor_can_use_other_rules(monkeypatch):
    async def fake_snapshot(server, *, cwd, timeout):
        return snapshot()

    monkeypatch.setattr(doctor, "snapshot_server", fake_snapshot)
    config = Config(servers=[StdioServerConfig(name="orders", transport="stdio", command="x")])
    rule = Rule("acme.always", "", Severity.WARN, lambda snap: [f"{len(snap.tools)} tools"])

    report = asyncio.run(run_doctor(config, rules=[rule]))

    assert report.servers[0].findings == (Finding("acme.always", Severity.WARN, "2 tools"),)


def test_servers_that_failed_to_start_are_not_checked_against_rules(monkeypatch):
    async def fake_snapshot(server, *, cwd, timeout):
        raise ServerError("boom")

    monkeypatch.setattr(doctor, "snapshot_server", fake_snapshot)
    config = Config(servers=[StdioServerConfig(name="orders", transport="stdio", command="x")])

    report = asyncio.run(run_doctor(config))

    assert report.servers[0].findings == ()


def test_http_servers_are_connected_to_not_started():
    report = DoctorReport(
        (ServerReport("docs", snapshot=snapshot(transport="http", seconds=0.087)),)
    )

    text = format_report(report)

    assert "  OK     connected in 87 ms (orders-server 1.2.3, MCP 2026-07-28), 2 tools" in text
    assert "started" not in text


AUTH_MESSAGE = (
    "authentication required: the server answered 401 (mcph cannot send credentials yet)"
)


def auth_report(**auth) -> DoctorReport:
    error = ServerError(AUTH_MESSAGE, auth=AuthInfo(**auth))
    return DoctorReport((ServerReport("docs", error=error),))


def test_a_server_that_requires_auth_is_explained():
    challenge = 'Bearer resource_metadata="http://h/prm"'
    report = auth_report(
        challenge=challenge,
        metadata_url="http://h/prm",
        authorization_servers=("https://auth.example.com/",),
        scopes=("api.read", "api.write"),
    )

    assert format_report(report) == "\n".join(
        [
            "Checking 1 server",
            "",
            "docs",
            f"  ERROR  {AUTH_MESSAGE}",
            f"         challenge: {challenge}",
            "         authorization server: https://auth.example.com/",
            "         scopes: api.read, api.write",
            "",
            "1 of 1 server failed.",
        ]
    )


def test_several_authorization_servers_are_listed_with_a_plural_label():
    report = auth_report(
        challenge='Bearer resource_metadata="http://h/prm"',
        metadata_url="http://h/prm",
        authorization_servers=("https://a/", "https://b/"),
    )

    text = format_report(report)

    assert "         authorization servers: https://a/, https://b/" in text
    assert "scopes:" not in text


def test_metadata_found_at_a_well_known_url_names_that_url():
    report = auth_report(
        challenge='Bearer realm="mcp"',
        metadata_url="http://h/.well-known/oauth-protected-resource/mcp",
        authorization_servers=("https://a/",),
    )

    text = format_report(report)

    assert '         challenge: Bearer realm="mcp"' in text
    assert "         metadata: http://h/.well-known/oauth-protected-resource/mcp" in text


def test_a_401_without_metadata_says_so():
    text = format_report(auth_report(challenge='Bearer realm="demo"'))

    assert '         challenge: Bearer realm="demo"' in text
    assert "         no OAuth protected resource metadata found" in text


def test_a_401_without_a_challenge_header_says_so():
    text = format_report(auth_report(challenge=None))

    assert "         challenge: none (the 401 had no WWW-Authenticate header)" in text
    assert "no OAuth protected resource metadata found" in text


def test_a_refused_token_is_listed_field_by_field():
    error = ServerError(
        "the server rejected the token in $DOCS_TOKEN (HTTP 401)",
        auth=AuthInfo(
            challenge='Bearer error="invalid_token", scope="api.write"',
            metadata_url="http://h/prm",
            authorization_servers=("https://auth.example.com/",),
            scopes=("api.read", "api.write"),
            error="invalid_token",
            error_description="The token expired",
            required_scope="api.write",
        ),
    )

    text = format_report(DoctorReport((ServerReport("docs", error=error),)))

    assert text == "\n".join(
        [
            "Checking 1 server",
            "",
            "docs",
            "  ERROR  the server rejected the token in $DOCS_TOKEN (HTTP 401)",
            "         error: invalid_token",
            "         server says: The token expired",
            "         scope the server asks for: api.write",
            "         metadata: http://h/prm",
            "         authorization server: https://auth.example.com/",
            "         scopes: api.read, api.write",
            "",
            "1 of 1 server failed.",
        ]
    )


def test_the_raw_challenge_is_left_out_when_its_parts_are_listed():
    text = format_report(
        auth_report(challenge='Bearer error="insufficient_scope"', error="insufficient_scope")
    )

    assert "         error: insufficient_scope" in text
    assert "challenge:" not in text
    assert "no OAuth protected resource metadata found" in text


def test_a_403_without_a_challenge_says_so():
    text = format_report(auth_report(challenge=None, status=403, error=None))

    assert "challenge: none (the 403 had no WWW-Authenticate header)" in text

