import asyncio
from pathlib import Path

from mcph import doctor
from mcph.auth_probe import AuthInfo
from mcph.config import (
    Config,
    DoctorConfig,
    HttpServerConfig,
    OAuthAuthConfig,
    StdioServerConfig,
)
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


# --- OAuth findings next to the explanation of why authentication failed --------------------


def test_oauth_findings_are_listed_under_the_failed_server():
    auth = AuthInfo(
        challenge='Bearer resource_metadata="http://h/prm"',
        metadata_url="http://h/prm",
        authorization_servers=("https://auth.example.com/",),
    )
    findings = (
        Finding("oauth.issuer", Severity.WARN, "they differ only by a trailing slash"),
        Finding("oauth.pkce", Severity.ERROR, "no S256"),
    )
    error = ServerError(AUTH_MESSAGE, auth=auth)
    report = DoctorReport((ServerReport("docs", error=error, findings=findings),))

    assert format_report(report) == "\n".join(
        [
            "Checking 1 server",
            "",
            "docs",
            f"  ERROR  {AUTH_MESSAGE}",
            '         challenge: Bearer resource_metadata="http://h/prm"',
            "         authorization server: https://auth.example.com/",
            "  WARN   oauth.issuer: they differ only by a trailing slash",
            "  ERROR  oauth.pkce: no S256",
            "",
            "1 of 1 server failed, 1 warning.",
        ]
    )


def failing_with_auth(monkeypatch, auth: AuthInfo | None):
    async def fake_snapshot(server, *, cwd, timeout):
        raise ServerError("authentication required", auth=auth)

    monkeypatch.setattr(doctor, "snapshot_server", fake_snapshot)


def record_audits(monkeypatch, findings=()):
    calls = []

    async def fake_audit(auth, url, *, timeout, issuer_slash_accepted):
        calls.append(
            {"auth": auth, "url": url, "timeout": timeout, "accepted": issuer_slash_accepted}
        )
        return findings

    monkeypatch.setattr(doctor, "audit_authorization_server", fake_audit)
    return calls


def http_config(**auth) -> Config:
    server = HttpServerConfig(
        name="docs",
        transport="http",
        url="http://x/mcp",
        auth=OAuthAuthConfig(type="oauth", **auth) if auth is not None else None,
    )
    return Config(servers=[server])


AUTH = AuthInfo(challenge=None, authorization_servers=("https://as.test/",))
FINDING = Finding("oauth.pkce", Severity.ERROR, "no S256")


def test_a_server_that_needs_authentication_gets_its_oauth_setup_checked(monkeypatch):
    failing_with_auth(monkeypatch, AUTH)
    calls = record_audits(monkeypatch, (FINDING,))

    report = asyncio.run(run_doctor(http_config(), timeout=9))

    assert report.servers[0].findings == (FINDING,)
    assert calls == [{"auth": AUTH, "url": "http://x/mcp", "timeout": 9, "accepted": False}]


def test_the_trailing_slash_setting_of_the_server_is_passed_on(monkeypatch):
    failing_with_auth(monkeypatch, AUTH)
    calls = record_audits(monkeypatch)

    asyncio.run(run_doctor(http_config(ignore_issuer_trailing_slash=True)))

    assert calls[0]["accepted"] is True


def test_a_server_without_oauth_settings_is_checked_with_the_strict_default(monkeypatch):
    failing_with_auth(monkeypatch, AUTH)
    calls = record_audits(monkeypatch)
    config = Config(
        servers=[HttpServerConfig(name="docs", transport="http", url="http://x/mcp")]
    )

    asyncio.run(run_doctor(config))

    assert calls[0]["accepted"] is False


def test_nothing_is_checked_when_the_server_did_not_say_anything_about_auth(monkeypatch):
    failing_with_auth(monkeypatch, None)
    calls = record_audits(monkeypatch, (FINDING,))

    report = asyncio.run(run_doctor(http_config()))

    assert calls == []
    assert report.servers[0].findings == ()


def test_stdio_servers_are_never_audited(monkeypatch):
    failing_with_auth(monkeypatch, AUTH)
    calls = record_audits(monkeypatch, (FINDING,))
    config = Config(servers=[StdioServerConfig(name="s", transport="stdio", command="x")])

    report = asyncio.run(run_doctor(config))

    assert calls == []
    assert report.servers[0].findings == ()


def test_servers_that_connected_are_not_audited(monkeypatch):
    async def fake_snapshot(server, *, cwd, timeout):
        return snapshot()

    monkeypatch.setattr(doctor, "snapshot_server", fake_snapshot)
    calls = record_audits(monkeypatch, (FINDING,))

    asyncio.run(run_doctor(http_config()))

    assert calls == []


# --- the rule settings of mcph.yaml ---------------------------------------------------------


def with_rules(config: Config, rules: dict[str, str]) -> Config:
    return config.model_copy(update={"doctor": DoctorConfig(rules=rules)})


def stdio_config(rules: dict[str, str]) -> Config:
    server = StdioServerConfig(name="orders", transport="stdio", command="x")
    return with_rules(Config(servers=[server]), rules)


def serving(monkeypatch, *tools: str) -> None:
    async def fake_snapshot(server, *, cwd, timeout):
        return snapshot(tools=tools)

    monkeypatch.setattr(doctor, "snapshot_server", fake_snapshot)


SNAKE_CASE = "core.tool_name_snake_case"


def test_a_rule_can_be_made_an_error(monkeypatch):
    serving(monkeypatch, "get_order", "getShipment")

    report = asyncio.run(run_doctor(stdio_config({SNAKE_CASE: "error"})))

    [finding] = report.servers[0].findings
    assert (finding.rule_id, finding.severity) == (SNAKE_CASE, Severity.ERROR)
    assert not report.ok
    assert "  ERROR  core.tool_name_snake_case: tool 'getShipment'" in format_report(report)


def test_a_rule_can_be_set_to_warn(monkeypatch):
    serving(monkeypatch, "getShipment")

    report = asyncio.run(run_doctor(stdio_config({SNAKE_CASE: "warn"})))

    assert [f.severity for f in report.servers[0].findings] == [Severity.WARN]
    assert report.ok and report.warning_count == 1


def test_a_rule_that_is_off_is_not_run_at_all(monkeypatch):
    serving(monkeypatch, "getShipment")
    asked = []
    spy = Rule(SNAKE_CASE, "", Severity.WARN, lambda snap: asked.append(snap) or ["found"])

    report = asyncio.run(run_doctor(stdio_config({SNAKE_CASE: "off"}), rules=[spy]))

    assert asked == []
    assert report.servers[0].findings == ()
    assert "All servers OK." in format_report(report)


def test_the_other_rules_still_run_when_one_is_off(monkeypatch):
    serving(monkeypatch, "getShipment")
    off = Rule(SNAKE_CASE, "", Severity.WARN, lambda snap: ["silenced"])
    on = Rule("acme.on", "", Severity.WARN, lambda snap: ["still here"])

    report = asyncio.run(run_doctor(stdio_config({SNAKE_CASE: "off"}), rules=[off, on]))

    assert report.servers[0].findings == (Finding("acme.on", Severity.WARN, "still here"),)


def test_rules_that_are_not_mentioned_keep_their_default_severity(monkeypatch):
    serving(monkeypatch, "getShipment")

    report = asyncio.run(run_doctor(stdio_config({"oauth.pkce": "error"})))

    assert [f.severity for f in report.servers[0].findings] == [Severity.WARN]


def test_the_settings_apply_to_every_server(monkeypatch):
    serving(monkeypatch, "getShipment")
    servers = [
        StdioServerConfig(name=name, transport="stdio", command="x") for name in ("a", "b")
    ]
    config = with_rules(Config(servers=servers), {SNAKE_CASE: "error"})

    report = asyncio.run(run_doctor(config))

    assert [s.ok for s in report.servers] == [False, False]


def test_oauth_findings_get_the_configured_severity_and_the_ones_that_are_off_vanish(
    monkeypatch,
):
    failing_with_auth(monkeypatch, AUTH)
    record_audits(
        monkeypatch,
        (
            Finding("oauth.pkce", Severity.ERROR, "no S256"),
            Finding("oauth.issuer", Severity.ERROR, "another issuer"),
            Finding("oauth.registration", Severity.WARN, "no registration"),
        ),
    )
    settings = {"oauth.pkce": "warn", "oauth.issuer": "off"}

    report = asyncio.run(run_doctor(with_rules(http_config(), settings)))

    assert report.servers[0].findings == (
        Finding("oauth.pkce", Severity.WARN, "no S256"),
        Finding("oauth.registration", Severity.WARN, "no registration"),
    )


def test_the_authorization_server_is_still_checked_while_one_oauth_rule_is_on(monkeypatch):
    failing_with_auth(monkeypatch, AUTH)
    registration = Finding("oauth.registration", Severity.WARN, "no registration")
    calls = record_audits(monkeypatch, (FINDING, registration))
    settings = {"oauth.metadata": "off", "oauth.issuer": "off", "oauth.pkce": "off"}

    report = asyncio.run(run_doctor(with_rules(http_config(), settings)))

    assert len(calls) == 1
    assert report.servers[0].findings == (registration,)  # FINDING is oauth.pkce: off


def test_the_authorization_server_is_not_contacted_when_every_oauth_rule_is_off(monkeypatch):
    failing_with_auth(monkeypatch, AUTH)
    calls = record_audits(monkeypatch, (FINDING,))
    settings = {
        "oauth.metadata": "off",
        "oauth.issuer": "off",
        "oauth.pkce": "off",
        "oauth.registration": "off",
    }

    report = asyncio.run(run_doctor(with_rules(http_config(), settings)))

    assert calls == []
    assert report.servers[0].findings == ()


def test_turning_off_a_rule_of_another_kind_does_not_stop_the_oauth_checks(monkeypatch):
    failing_with_auth(monkeypatch, AUTH)
    calls = record_audits(monkeypatch, (FINDING,))

    report = asyncio.run(run_doctor(with_rules(http_config(), {SNAKE_CASE: "off"})))

    assert len(calls) == 1
    assert report.servers[0].findings == (FINDING,)
