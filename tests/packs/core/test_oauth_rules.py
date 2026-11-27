import asyncio

import pytest

from mcph.auth_probe import AuthInfo
from mcph.config import HttpServerConfig, OAuthAuthConfig
from mcph.mcp_client import ServerError
from mcph.oauth_audit import Discovery
from mcph.rules import NO_CONNECTION, Outcome, RuleContext, RuleResult, Severity, run_rule

# What the user sees as the reason of a skipped check.
NOT_NEEDED = "not needed, the server accepted the connection"
NO_AUTHORIZATION_SERVER = "the server announced no authorization server"
NO_METADATA = "the authorization server's metadata was not found"

TIMEOUT = 7.0
RESOURCE = "https://rs.test/mcp"
ANNOUNCED = "https://as.test"
METADATA_URL = "https://as.test/.well-known/oauth-authorization-server"
OAUTH_IDS = ["oauth.metadata", "oauth.issuer", "oauth.pkce", "oauth.registration"]

GOOD = {
    "issuer": ANNOUNCED,
    "authorization_endpoint": "https://as.test/authorize",
    "token_endpoint": "https://as.test/token",
    "registration_endpoint": "https://as.test/register",
    "code_challenge_methods_supported": ["S256"],
}


class Lookups:
    """A stand-in for the metadata lookup that records how often it was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float]] = []
        self.discovery = Discovery(METADATA_URL, GOOD, (f"{METADATA_URL} (200)",))

    def finds(self, **changes) -> None:
        metadata = {**GOOD, **changes}
        self.discovery = Discovery(
            METADATA_URL,
            {key: value for key, value in metadata.items() if value is not None},
            (f"{METADATA_URL} (200)",),
        )

    def finds_nothing(self) -> None:
        self.discovery = Discovery(None, None, (f"{METADATA_URL} (404)",))


@pytest.fixture
def lookups(monkeypatch, core_rules):
    lookups = Lookups()

    async def fake(announced, resource_url, *, timeout, transport=None):
        lookups.calls.append((announced, resource_url, timeout))
        return lookups.discovery

    # The rules look the function up in the namespace of the file that defines them.
    namespace = core_rules["oauth.metadata"].check.__globals__
    monkeypatch.setitem(namespace, "discover_metadata", fake)
    return lookups


def server(**auth) -> HttpServerConfig:
    return HttpServerConfig(
        name="docs",
        transport="http",
        url=RESOURCE,
        auth=OAuthAuthConfig(type="oauth", **auth),
    )


def refused(*authorization_servers: str, **auth) -> RuleContext:
    """The context of an http server that asked for authentication."""
    info = AuthInfo(challenge=None, authorization_servers=authorization_servers)
    error = ServerError("authentication required", auth=info)
    return RuleContext(server(**auth), TIMEOUT, error=error)


@pytest.fixture
def check(core_rules):
    """Run the four oauth.* rules on a context and give their results by rule id."""
    rules = [core_rules[rule_id] for rule_id in OAUTH_IDS]

    def run(context: RuleContext) -> dict[str, RuleResult]:
        async def run_all():
            return {rule.id: await run_rule(rule, context) for rule in rules}

        return asyncio.run(run_all())

    return run


def findings_of(results: dict[str, RuleResult]) -> list:
    return [finding for result in results.values() for finding in result.findings]


def test_nothing_is_looked_up_for_a_server_that_accepted_the_connection(
    lookups, snapshot_with, check
):
    context = RuleContext(server(), TIMEOUT, snapshot=snapshot_with("get_x"))

    results = check(context)

    assert results == {rule_id: RuleResult.skipped(rule_id, NOT_NEEDED) for rule_id in OAUTH_IDS}
    assert lookups.calls == []


def test_nothing_is_looked_up_when_the_server_did_not_say_anything_about_auth(lookups, check):
    context = RuleContext(server(), TIMEOUT, error=ServerError("connection refused"))

    results = check(context)

    assert results == {rule_id: RuleResult.skipped(rule_id, NO_CONNECTION) for rule_id in OAUTH_IDS}
    assert lookups.calls == []


def test_nothing_is_looked_up_when_no_authorization_server_was_announced(lookups, check):
    results = check(refused())

    assert results == {
        rule_id: RuleResult.skipped(rule_id, NO_AUTHORIZATION_SERVER) for rule_id in OAUTH_IDS
    }
    assert lookups.calls == []


def test_a_sound_authorization_server_passes_every_check(lookups, check):
    results = check(refused(ANNOUNCED))

    assert results == {rule_id: RuleResult(rule_id) for rule_id in OAUTH_IDS}
    assert all(result.outcome is Outcome.RAN for result in results.values())


def test_the_authorization_server_is_looked_up_once_for_all_four_rules(lookups, check):
    check(refused(ANNOUNCED))

    assert lookups.calls == [(ANNOUNCED, RESOURCE, TIMEOUT)]


def test_the_first_announced_authorization_server_is_the_one_checked(lookups, check):
    check(refused(ANNOUNCED, "https://other.test"))

    assert [call[0] for call in lookups.calls] == [ANNOUNCED]


def test_each_finding_is_in_the_result_of_its_own_rule(lookups, check):
    lookups.finds(code_challenge_methods_supported=None, registration_endpoint=None)

    results = check(refused(ANNOUNCED))

    assert results["oauth.metadata"] == RuleResult("oauth.metadata")
    assert results["oauth.issuer"] == RuleResult("oauth.issuer")
    assert [f.rule_id for f in results["oauth.pkce"].findings] == ["oauth.pkce"]
    assert [f.severity for f in results["oauth.pkce"].findings] == [Severity.ERROR]
    assert [f.rule_id for f in results["oauth.registration"].findings] == ["oauth.registration"]
    assert [f.severity for f in results["oauth.registration"].findings] == [Severity.WARN]


def test_a_trailing_slash_difference_keeps_its_own_severity(lookups, check):
    lookups.finds(issuer="https://as.test")  # announced with a slash, says it has none

    results = check(refused("https://as.test/"))

    [finding] = results["oauth.issuer"].findings
    assert finding.severity is Severity.WARN  # the rule's default is an error
    assert "set `ignore_issuer_trailing_slash: true`" in finding.message


def test_the_trailing_slash_setting_of_the_server_is_passed_on(lookups, check):
    lookups.finds(issuer="https://as.test")

    [finding] = check(refused("https://as.test/", ignore_issuer_trailing_slash=True))[
        "oauth.issuer"
    ].findings

    assert "makes mcph accept" in finding.message


def test_a_server_without_oauth_settings_gets_the_strict_default(lookups, check):
    lookups.finds(issuer="https://as.test")
    info = AuthInfo(challenge=None, authorization_servers=("https://as.test/",))
    plain = HttpServerConfig(name="docs", transport="http", url=RESOURCE)
    context = RuleContext(plain, TIMEOUT, error=ServerError("401", auth=info))

    [finding] = check(context)["oauth.issuer"].findings

    assert "set `ignore_issuer_trailing_slash: true`" in finding.message


def test_without_metadata_only_finding_it_is_checked_and_the_other_checks_are_skipped(lookups, check):
    lookups.finds_nothing()

    results = check(refused(ANNOUNCED))

    metadata = results["oauth.metadata"]
    assert metadata.outcome is Outcome.RAN
    assert [f.severity for f in metadata.findings] == [Severity.ERROR]
    assert "cannot find the metadata" in metadata.findings[0].message
    assert [results[rule_id] for rule_id in OAUTH_IDS[1:]] == [
        RuleResult.skipped(rule_id, NO_METADATA) for rule_id in OAUTH_IDS[1:]
    ]


def test_every_finding_comes_from_the_rule_that_reports_it(lookups, check):
    lookups.finds(
        authorization_endpoint=None,
        issuer="https://evil.test",
        code_challenge_methods_supported=None,
        registration_endpoint=None,
    )

    results = check(refused(ANNOUNCED))

    assert {rule_id: [f.rule_id for f in r.findings] for rule_id, r in results.items()} == {
        rule_id: [rule_id] for rule_id in OAUTH_IDS
    }
    assert len(findings_of(results)) == 4
