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
from mcph.doctor import NO_RULES, DoctorReport, ServerReport, format_report
from mcph.extensions import load_pack, resolve_pack
from mcph.mcp_client import ServerError, ServerSnapshot, ToolInfo
from mcph.oauth_audit import Discovery
from mcph.rules import NO_CONNECTION, Finding, Rule, RuleResult, Severity, SkipRule

OAUTH_IDS = ["oauth.metadata", "oauth.issuer", "oauth.pkce", "oauth.registration"]
NOT_NEEDED = "not needed, the server accepted the connection"  # as the user sees it

CORE_RULES = load_pack(resolve_pack("builtin:core", Path.cwd())).rules
# The oauth.* rules look their helpers up in the namespace of the file that defines them.
OAUTH_NAMESPACE = next(rule for rule in CORE_RULES if rule.id == "oauth.metadata").check.__globals__


def run_doctor(config, **kwargs):
    """``mcph.doctor.run_doctor`` with the rules of the core pack unless others are given."""
    kwargs.setdefault("rules", CORE_RULES)
    return doctor.run_doctor(config, **kwargs)


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


def reported(*findings: Finding) -> tuple[RuleResult, ...]:
    """The results of the rules that reported these findings, one per rule id."""
    ids = dict.fromkeys(finding.rule_id for finding in findings)
    return tuple(
        RuleResult(rule_id, findings=tuple(f for f in findings if f.rule_id == rule_id))
        for rule_id in ids
    )


def test_findings_are_listed_under_their_server():
    report = DoctorReport(
        (ServerReport("orders", snapshot=snapshot(), rules=reported(WARNING)),)
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
            ServerReport("a", snapshot=snapshot(), rules=reported(*two_warnings)),
            ServerReport("b", error=ServerError("boom")),
        )
    )

    text = format_report(report)

    assert text.endswith("1 of 2 servers failed, 2 warnings.")


def test_error_findings_fail_the_server_and_are_labelled_error():
    problem = Finding("acme.rule", Severity.ERROR, "something is wrong")
    report = DoctorReport(
        (ServerReport("orders", snapshot=snapshot(), rules=reported(problem)),)
    )

    text = format_report(report)

    assert not report.ok
    assert "  ERROR  acme.rule: something is wrong" in text
    assert text.endswith("1 of 1 server failed.")


def test_passed_treats_warnings_as_failures_only_when_strict():
    clean = DoctorReport((ServerReport("a", snapshot=snapshot()),))
    warned = DoctorReport((ServerReport("a", snapshot=snapshot(), rules=reported(WARNING)),))
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
    rule = Rule("acme.always", "", Severity.WARN, lambda context: [f"{len(context.snapshot.tools)} tools"])

    report = asyncio.run(run_doctor(config, rules=[rule]))

    assert report.servers[0].findings == (Finding("acme.always", Severity.WARN, "2 tools"),)


def test_servers_that_failed_to_start_are_not_checked_against_rules(monkeypatch):
    async def fake_snapshot(server, *, cwd, timeout):
        raise ServerError("boom")

    monkeypatch.setattr(doctor, "snapshot_server", fake_snapshot)
    config = Config(servers=[StdioServerConfig(name="orders", transport="stdio", command="x")])

    report = asyncio.run(run_doctor(config))

    assert report.servers[0].findings == ()
    assert report.servers[0].rules == (RuleResult.skipped(SNAKE_CASE, NO_CONNECTION),)


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
    report = DoctorReport((ServerReport("docs", error=error, rules=reported(*findings)),))

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


def audited(findings=()) -> tuple[RuleResult, ...]:
    """What the oauth.* rules answer when the checks find ``findings``."""
    return tuple(
        RuleResult(rule_id, findings=tuple(f for f in findings if f.rule_id == rule_id))
        for rule_id in OAUTH_IDS
    )


def record_audits(monkeypatch, findings=()):
    """Replace the metadata lookup and the checks behind the oauth.* rules.

    The checks find ``findings``; the returned list records each lookup.
    """
    calls = []

    async def fake_discover(announced, resource_url, *, timeout, transport=None):
        calls.append({"announced": announced, "url": resource_url, "timeout": timeout})
        return Discovery("https://as.test/.well-known/oauth-authorization-server", {}, ())

    def fake_check(announced, discovery, *, issuer_slash_accepted=False):
        calls[-1]["accepted"] = issuer_slash_accepted
        return list(findings)

    monkeypatch.setitem(OAUTH_NAMESPACE, "discover_metadata", fake_discover)
    monkeypatch.setitem(OAUTH_NAMESPACE, "check_metadata", fake_check)
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
    assert report.servers[0].rules == (
        RuleResult.skipped(SNAKE_CASE, NO_CONNECTION),
        *audited((FINDING,)),
    )
    assert calls == [
        {"announced": "https://as.test/", "url": "http://x/mcp", "timeout": 9, "accepted": False}
    ]


def test_nothing_is_checked_when_the_server_did_not_say_anything_about_auth(monkeypatch):
    failing_with_auth(monkeypatch, None)
    calls = record_audits(monkeypatch, (FINDING,))

    report = asyncio.run(run_doctor(http_config()))

    assert calls == []
    assert report.servers[0].findings == ()
    assert report.servers[0].rules == (
        RuleResult.skipped(SNAKE_CASE, NO_CONNECTION),
        *(RuleResult.skipped(rule_id, NO_CONNECTION) for rule_id in OAUTH_IDS),
    )


def test_stdio_servers_are_never_audited(monkeypatch):
    failing_with_auth(monkeypatch, AUTH)
    calls = record_audits(monkeypatch, (FINDING,))
    config = Config(servers=[StdioServerConfig(name="s", transport="stdio", command="x")])

    report = asyncio.run(run_doctor(config))

    assert calls == []
    assert report.servers[0].findings == ()
    assert [r.rule_id for r in report.servers[0].rules] == [SNAKE_CASE]  # no oauth.* rules


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
    spy = Rule(SNAKE_CASE, "", Severity.WARN, lambda context: asked.append(context) or ["found"])

    report = asyncio.run(run_doctor(stdio_config({SNAKE_CASE: "off"}), rules=[spy]))

    assert asked == []
    assert report.servers[0].findings == ()
    assert report.servers[0].rules == (RuleResult.off(SNAKE_CASE),)
    assert "All servers OK." in format_report(report)


def test_the_other_rules_still_run_when_one_is_off(monkeypatch):
    serving(monkeypatch, "getShipment")
    off = Rule(SNAKE_CASE, "", Severity.WARN, lambda context: ["silenced"])
    on = Rule("acme.on", "", Severity.WARN, lambda context: ["still here"])

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
    assert report.servers[0].rules[1:] == tuple(RuleResult.off(rule_id) for rule_id in OAUTH_IDS)


def test_turning_off_a_rule_of_another_kind_does_not_stop_the_oauth_checks(monkeypatch):
    failing_with_auth(monkeypatch, AUTH)
    calls = record_audits(monkeypatch, (FINDING,))

    report = asyncio.run(run_doctor(with_rules(http_config(), {SNAKE_CASE: "off"})))

    assert len(calls) == 1
    assert report.servers[0].findings == (FINDING,)


# --- what happened to each rule -------------------------------------------------------------


def test_a_rule_that_passed_is_listed_as_ok():
    report = DoctorReport(
        (ServerReport("orders", snapshot=snapshot(), rules=(RuleResult(SNAKE_CASE),)),)
    )

    assert format_report(report) == "\n".join(
        [
            "Checking 1 server",
            "",
            "orders",
            "  OK     started in 312 ms (orders-server 1.2.3, MCP 2026-07-28), 2 tools",
            "  OK     core.tool_name_snake_case",
            "",
            "All servers OK.",
        ]
    )


def test_a_rule_that_found_problems_is_listed_by_its_findings_and_not_as_ok():
    rules = (RuleResult(SNAKE_CASE, findings=(WARNING, WARNING)), RuleResult("acme.fine"))
    report = DoctorReport((ServerReport("orders", snapshot=snapshot(), rules=rules),))

    lines = format_report(report).splitlines()

    assert lines[4:7] == [
        "  WARN   core.tool_name_snake_case: tool 'getOrder' is not snake_case (try 'get_order')",
        "  WARN   core.tool_name_snake_case: tool 'getOrder' is not snake_case (try 'get_order')",
        "  OK     acme.fine",
    ]
    assert "  OK     core.tool_name_snake_case" not in lines


def test_a_rule_that_is_off_is_listed_as_off():
    rules = (RuleResult(SNAKE_CASE), RuleResult.off("oauth.pkce"))
    report = DoctorReport((ServerReport("docs", snapshot=snapshot(), rules=rules),))

    lines = format_report(report).splitlines()

    assert lines[4:6] == ["  OK     core.tool_name_snake_case", "  OFF    oauth.pkce"]
    assert report.ok and report.warning_count == 0


def test_skipped_rules_share_a_line_per_reason_where_the_first_of_them_is():
    rules = (
        RuleResult.skipped("core.a", "no tools"),
        RuleResult("core.b"),
        RuleResult.skipped("oauth.x", "no tools"),
        RuleResult.skipped("oauth.y", "no metadata"),
        RuleResult.off("oauth.z"),
        RuleResult.skipped("oauth.w", "no tools"),
    )
    report = DoctorReport((ServerReport("docs", snapshot=snapshot(), rules=rules),))

    assert format_report(report).splitlines()[4:8] == [
        "  SKIP   core.a, oauth.x, oauth.w: no tools",
        "  OK     core.b",
        "  SKIP   oauth.y: no metadata",
        "  OFF    oauth.z",
    ]
    assert report.ok and report.warning_count == 0


def test_the_rules_of_a_failed_server_follow_its_error_and_details():
    error = ServerError("failed while starting the server: Connection closed", "boom")
    rules = (RuleResult.skipped(SNAKE_CASE, NO_CONNECTION),)
    report = DoctorReport((ServerReport("legacy", error=error, rules=rules),))

    assert format_report(report) == "\n".join(
        [
            "Checking 1 server",
            "",
            "legacy",
            "  ERROR  failed while starting the server: Connection closed",
            "         server stderr:",
            "           boom",
            "  SKIP   core.tool_name_snake_case: mcph could not connect to the server",
            "",
            "1 of 1 server failed.",
        ]
    )


def test_a_report_without_rule_results_lists_none():
    report = DoctorReport((ServerReport("orders", snapshot=snapshot()),))

    assert format_report(report).splitlines()[2:] == [
        "orders",
        "  OK     started in 312 ms (orders-server 1.2.3, MCP 2026-07-28), 2 tools",
        "",
        "All servers OK.",
    ]


def test_every_rule_that_ran_on_a_stdio_server_has_a_result(monkeypatch):
    serving(monkeypatch, "get_order", "getShipment")

    report = asyncio.run(run_doctor(stdio_config({})))

    [result] = report.servers[0].rules
    assert result.rule_id == SNAKE_CASE
    assert [f.message for f in result.findings] == [
        "tool 'getShipment' is not snake_case (try 'get_shipment')"
    ]


def test_a_rule_that_found_nothing_is_a_result_without_findings(monkeypatch):
    serving(monkeypatch, "get_order")

    report = asyncio.run(run_doctor(stdio_config({})))

    assert report.servers[0].rules == (RuleResult(SNAKE_CASE),)


def test_an_http_server_that_connected_skips_the_oauth_checks(monkeypatch):
    serving(monkeypatch, "get_order")
    calls = record_audits(monkeypatch, (FINDING,))

    report = asyncio.run(run_doctor(http_config()))

    assert calls == []
    assert report.servers[0].rules == (
        RuleResult(SNAKE_CASE),
        *(RuleResult.skipped(rule_id, NOT_NEEDED) for rule_id in OAUTH_IDS),
    )


def test_an_oauth_check_that_is_off_is_listed_as_off_among_the_skipped_ones(monkeypatch):
    serving(monkeypatch, "get_order")
    config = with_rules(http_config(), {"oauth.registration": "off"})

    report = asyncio.run(run_doctor(config))

    assert report.servers[0].rules == (
        RuleResult(SNAKE_CASE),
        *(RuleResult.skipped(rule_id, NOT_NEEDED) for rule_id in OAUTH_IDS[:-1]),
        RuleResult.off("oauth.registration"),
    )


def test_every_oauth_check_that_is_off_is_listed_as_off_for_a_connected_server(monkeypatch):
    serving(monkeypatch, "get_order")
    config = with_rules(http_config(), dict.fromkeys(OAUTH_IDS, "off"))

    report = asyncio.run(run_doctor(config))

    assert report.servers[0].rules == (
        RuleResult(SNAKE_CASE),
        *(RuleResult.off(rule_id) for rule_id in OAUTH_IDS),
    )


def test_a_rule_that_is_off_is_listed_as_off_even_for_a_server_that_failed(monkeypatch):
    failing_with_auth(monkeypatch, None)
    config = with_rules(http_config(), {SNAKE_CASE: "off", "oauth.pkce": "off"})

    report = asyncio.run(run_doctor(config))

    assert report.servers[0].rules == (
        RuleResult.off(SNAKE_CASE),
        RuleResult.skipped("oauth.metadata", NO_CONNECTION),
        RuleResult.skipped("oauth.issuer", NO_CONNECTION),
        RuleResult.off("oauth.pkce"),
        RuleResult.skipped("oauth.registration", NO_CONNECTION),
    )


def test_the_results_follow_the_order_of_the_rules_whatever_happened_to_each(monkeypatch):
    serving(monkeypatch, "getShipment")

    def skip(context):
        raise SkipRule("no reason")

    rules = [
        Rule("a.found", "", Severity.WARN, lambda context: ["problem"]),
        Rule("a.skipped", "", Severity.WARN, skip),
        Rule(SNAKE_CASE, "", Severity.WARN, lambda context: ["silenced"]),  # turned off below
        Rule("a.passed", "", Severity.WARN, lambda context: []),
    ]

    report = asyncio.run(run_doctor(stdio_config({SNAKE_CASE: "off"}), rules=rules))

    assert report.servers[0].rules == (
        RuleResult("a.found", findings=(Finding("a.found", Severity.WARN, "problem"),)),
        RuleResult.skipped("a.skipped", "no reason"),
        RuleResult.off(SNAKE_CASE),
        RuleResult("a.passed"),
    )


def test_a_rule_that_raises_is_an_error_and_the_other_rules_still_run(monkeypatch):
    serving(monkeypatch, "get_order")

    def broken(context):
        raise ValueError("boom")

    rules = [
        Rule("a.broken", "", Severity.WARN, broken),
        Rule("a.fine", "", Severity.WARN, lambda context: []),
    ]

    report = asyncio.run(run_doctor(stdio_config({}), rules=rules))

    [failure] = report.servers[0].findings
    assert (failure.rule_id, failure.severity) == ("a.broken", Severity.ERROR)
    assert "the rule failed: ValueError: boom" in failure.message
    assert report.servers[0].rules[1] == RuleResult("a.fine")
    assert not report.ok


def test_a_rule_only_applies_to_the_kinds_of_server_it_is_about(monkeypatch):
    serving(monkeypatch, "get_order")
    rules = [
        Rule("a.everywhere", "", Severity.WARN, lambda context: []),
        Rule("a.http_only", "", Severity.WARN, lambda context: [], transports=("http",)),
    ]

    stdio = asyncio.run(run_doctor(stdio_config({}), rules=rules))
    http = asyncio.run(run_doctor(http_config(), rules=rules))

    assert [r.rule_id for r in stdio.servers[0].rules] == ["a.everywhere"]
    assert [r.rule_id for r in http.servers[0].rules] == ["a.everywhere", "a.http_only"]


# --- a run without any rule -------------------------------------------------------------------


def test_without_any_rule_the_servers_are_only_reached_and_the_report_warns(monkeypatch):
    serving(monkeypatch, "getShipment")

    report = asyncio.run(doctor.run_doctor(stdio_config({})))  # no rules given

    assert report.servers[0].rules == ()
    assert report.warnings == (NO_RULES,)
    assert report.warning_count == 1
    assert report.ok  # a warning, not an error
    assert not report.passed(strict=True) and report.passed()


def test_the_warning_explains_how_to_get_the_rules_that_ship_with_mcph():
    assert NO_RULES == (
        "no rules are loaded, so the servers were only reached, not checked: add a pack "
        "to `extends` in mcph.yaml (`extends: [builtin:core]` has the rules that ship "
        "with mcph)."
    )


def test_the_report_shows_the_warning_before_the_summary_and_counts_it():
    report = DoctorReport(
        (ServerReport("orders", snapshot=snapshot()),), warnings=("something about the run.",)
    )

    assert format_report(report).splitlines()[-4:] == [
        "",
        "Warning: something about the run.",
        "",
        "All servers OK, 1 warning.",
    ]


def test_run_level_warnings_are_added_to_the_warnings_of_the_servers():
    report = DoctorReport(
        (ServerReport("a", snapshot=snapshot(), rules=reported(WARNING)),),
        warnings=("one", "two"),
    )

    assert report.warning_count == 3
    assert format_report(report).endswith("All servers OK, 3 warnings.")


def test_rules_that_are_all_turned_off_are_not_a_missing_pack(monkeypatch):
    serving(monkeypatch, "get_order")

    report = asyncio.run(run_doctor(stdio_config({SNAKE_CASE: "off"})))

    assert report.warnings == ()
    assert report.servers[0].rules == (RuleResult.off(SNAKE_CASE),)
