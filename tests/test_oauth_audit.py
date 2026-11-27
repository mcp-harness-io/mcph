import asyncio

import httpx2
import pytest

from mcph.auth_probe import AuthInfo
from mcph.oauth_audit import (
    NO_AUTHORIZATION_SERVER,
    NO_METADATA,
    Discovery,
    audit_authorization_server,
    check_metadata,
    discover_metadata,
)
from mcph.rules import OAUTH_RULES, Finding, Outcome, RuleResult, Severity

RESOURCE = "https://rs.test/mcp"
ANNOUNCED = "https://as.test"
ROOT_8414 = "https://as.test/.well-known/oauth-authorization-server"
ROOT_OIDC = "https://as.test/.well-known/openid-configuration"

GOOD = {
    "issuer": ANNOUNCED,
    "authorization_endpoint": "https://as.test/authorize",
    "token_endpoint": "https://as.test/token",
    "registration_endpoint": "https://as.test/register",
    "code_challenge_methods_supported": ["S256", "plain"],
}


def found(metadata: dict, url: str = ROOT_8414) -> Discovery:
    return Discovery(url, metadata, (f"{url} (200)",))


def changed(**changes) -> dict:
    """GOOD with some keys replaced; a value of ``None`` removes the key."""
    metadata = {**GOOD, **changes}
    return {k: v for k, v in metadata.items() if v is not None}


def ids(findings: list[Finding]) -> list[tuple[str, Severity]]:
    return [(finding.rule_id, finding.severity) for finding in findings]


# --- the checks ----------------------------------------------------------------------------


def test_a_sound_authorization_server_has_no_findings():
    assert check_metadata(ANNOUNCED, found(GOOD)) == []


def test_metadata_that_cannot_be_found_names_every_url_that_was_tried():
    discovery = Discovery(None, None, (f"{ROOT_8414} (404)", f"{ROOT_OIDC} (ConnectError)"))

    [finding] = check_metadata(ANNOUNCED, discovery)

    assert (finding.rule_id, finding.severity) == ("oauth.metadata", Severity.ERROR)
    assert finding.message == (
        f"cannot find the metadata of the authorization server {ANNOUNCED} "
        f"(tried: {ROOT_8414} (404); {ROOT_OIDC} (ConnectError))"
    )


def test_a_discovery_that_tried_nothing_says_so():
    [finding] = check_metadata(ANNOUNCED, Discovery(None, None, ()))

    assert finding.message.endswith("(tried: nothing)")


@pytest.mark.parametrize(
    ("changes", "missing"),
    [
        ({"authorization_endpoint": None}, "authorization_endpoint"),
        ({"token_endpoint": None}, "token_endpoint"),
        ({"token_endpoint": ""}, "token_endpoint"),
        ({"authorization_endpoint": 5}, "authorization_endpoint"),
        (
            {"authorization_endpoint": None, "token_endpoint": None},
            "authorization_endpoint or token_endpoint",
        ),
    ],
)
def test_the_endpoints_a_client_needs_must_be_there(changes, missing):
    [finding] = check_metadata(ANNOUNCED, found(changed(**changes)))

    assert (finding.rule_id, finding.severity) == ("oauth.metadata", Severity.ERROR)
    assert finding.message == f"the metadata at {ROOT_8414} has no {missing}"


def test_metadata_without_an_issuer_is_reported():
    [finding] = check_metadata(ANNOUNCED, found(changed(issuer=None)))

    assert (finding.rule_id, finding.severity) == ("oauth.issuer", Severity.ERROR)
    assert finding.message == f"the metadata at {ROOT_8414} has no issuer"


@pytest.mark.parametrize(
    "issuer", ["https://evil.test", "http://as.test", "https://as.test:8443", "https://as.test/x"]
)
def test_another_issuer_is_an_error(issuer):
    [finding] = check_metadata(ANNOUNCED, found(changed(issuer=issuer)))

    assert (finding.rule_id, finding.severity) == ("oauth.issuer", Severity.ERROR)
    assert finding.message == (
        f"the resource announces its authorization server as {ANNOUNCED} "
        f"but that server's metadata says {issuer}; RFC 8414 requires them to be "
        "identical, so clients refuse to sign in"
    )


@pytest.mark.parametrize(
    ("announced", "issuer"),
    [("https://as.test/", "https://as.test"), ("https://as.test", "https://as.test/")],
)
def test_a_trailing_slash_difference_is_a_warning_that_names_the_setting(announced, issuer):
    [finding] = check_metadata(announced, found(changed(issuer=issuer)))

    assert (finding.rule_id, finding.severity) == ("oauth.issuer", Severity.WARN)
    assert finding.message == (
        f"the resource announces its authorization server as {announced} "
        f"but that server's metadata says {issuer}; they differ only by a trailing "
        "slash, which strict clients refuse (RFC 8414); set "
        "`ignore_issuer_trailing_slash: true` under `auth` to have mcph accept it"
    )


def test_a_trailing_slash_difference_that_the_config_accepts_says_so():
    [finding] = check_metadata(
        "https://as.test/", found(changed(issuer="https://as.test")), issuer_slash_accepted=True
    )

    assert finding.severity is Severity.WARN
    assert finding.message.endswith(
        "they differ only by a trailing slash, which "
        "`ignore_issuer_trailing_slash` makes mcph accept"
    )


def test_a_path_issuer_with_a_trailing_slash_difference_is_not_the_tolerated_case():
    [finding] = check_metadata(
        "https://as.test/realms/x/", found(changed(issuer="https://as.test/realms/x"))
    )

    assert finding.severity is Severity.ERROR


@pytest.mark.parametrize(
    ("methods", "shown"),
    [
        (None, "absent"),
        (["plain"], "plain"),
        ([], "empty"),
        (["s256"], "s256"),  # case matters
        ("S256", "absent"),  # not a list
    ],
)
def test_pkce_with_s256_must_be_advertised(methods, shown):
    [finding] = check_metadata(
        ANNOUNCED, found(changed(code_challenge_methods_supported=methods))
    )

    assert (finding.rule_id, finding.severity) == ("oauth.pkce", Severity.ERROR)
    assert finding.message == (
        "the authorization server does not advertise PKCE with S256 "
        f"(code_challenge_methods_supported: {shown}); MCP clients require it"
    )


@pytest.mark.parametrize("registration", [None, ""])
def test_a_missing_registration_endpoint_is_a_warning(registration):
    [finding] = check_metadata(ANNOUNCED, found(changed(registration_endpoint=registration)))

    assert (finding.rule_id, finding.severity) == ("oauth.registration", Severity.WARN)
    assert "offers no registration_endpoint" in finding.message
    assert "mcph cannot sign in without dynamic client registration yet" in finding.message


def test_every_problem_is_reported_in_a_stable_order():
    broken = changed(
        authorization_endpoint=None,
        issuer="https://evil.test",
        code_challenge_methods_supported=None,
        registration_endpoint=None,
    )

    assert ids(check_metadata(ANNOUNCED, found(broken))) == [
        ("oauth.metadata", Severity.ERROR),
        ("oauth.issuer", Severity.ERROR),
        ("oauth.pkce", Severity.ERROR),
        ("oauth.registration", Severity.WARN),
    ]


def test_every_finding_comes_from_a_declared_rule_with_its_default_severity():
    # `oauth.*` rules are configurable in mcph.yaml, so the audit and the declared rules
    # must agree on the ids and on how serious each problem is by default.
    broken = changed(
        authorization_endpoint=None,
        issuer="https://evil.test",
        code_challenge_methods_supported=None,
        registration_endpoint=None,
    )

    findings = check_metadata(ANNOUNCED, found(broken))

    assert {f.rule_id: f.severity for f in findings} == {
        rule.id: rule.severity for rule in OAUTH_RULES
    }


# --- looking the metadata up ------------------------------------------------------------------


class Server:
    """A mock HTTP server answering from a {url: response} table (404 otherwise)."""

    def __init__(self, responses: dict[str, httpx2.Response]):
        self.responses = responses
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.responses.get(str(request.url), httpx2.Response(404))

    @property
    def urls(self) -> list[str]:
        return [str(request.url) for request in self.requests]


def discover(handler, announced=ANNOUNCED, *, timeout=5.0) -> Discovery:
    transport = httpx2.MockTransport(handler)
    return asyncio.run(
        discover_metadata(announced, RESOURCE, timeout=timeout, transport=transport)
    )


def test_the_first_url_that_answers_wins():
    server = Server({ROOT_8414: httpx2.Response(200, json=GOOD)})

    discovery = discover(server)

    assert discovery.url == ROOT_8414 and discovery.metadata == GOOD
    assert discovery.tried == (f"{ROOT_8414} (200)",)
    assert server.urls == [ROOT_8414]  # it stops there


def test_openid_connect_discovery_is_the_fallback():
    server = Server({ROOT_OIDC: httpx2.Response(200, json=GOOD)})

    discovery = discover(server)

    assert discovery.url == ROOT_OIDC
    assert discovery.tried == (f"{ROOT_8414} (404)", f"{ROOT_OIDC} (200)")


def test_an_issuer_with_a_path_is_looked_up_the_way_clients_do():
    server = Server({})

    discovery = discover(server, "https://as.test/realms/x")

    assert server.urls == [
        "https://as.test/.well-known/oauth-authorization-server/realms/x",
        "https://as.test/.well-known/openid-configuration/realms/x",
        "https://as.test/realms/x/.well-known/openid-configuration",
    ]
    assert discovery.metadata is None


def test_a_trailing_slash_on_a_root_issuer_does_not_change_where_it_is_looked_up():
    server = Server({})

    discover(server, "https://as.test/")

    assert server.urls == [ROOT_8414, ROOT_OIDC]


def test_what_went_wrong_at_each_url_is_recorded():
    def handler(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == ROOT_8414:
            return httpx2.Response(200, text="<html>not json</html>")
        raise httpx2.ConnectError("refused")

    discovery = discover(handler)

    assert discovery.metadata is None
    assert discovery.tried == (f"{ROOT_8414} (200, not JSON)", f"{ROOT_OIDC} (ConnectError)")


def test_a_json_document_that_is_not_an_object_is_skipped():
    server = Server(
        {ROOT_8414: httpx2.Response(200, json=["no"]), ROOT_OIDC: httpx2.Response(200, json=GOOD)}
    )

    discovery = discover(server)

    assert discovery.url == ROOT_OIDC
    assert discovery.tried[0] == f"{ROOT_8414} (200, not a JSON object)"


def test_a_server_error_is_recorded_with_its_status():
    server = Server({ROOT_8414: httpx2.Response(503), ROOT_OIDC: httpx2.Response(500)})

    assert discover(server).tried == (f"{ROOT_8414} (503)", f"{ROOT_OIDC} (500)")


def test_a_server_that_is_too_slow_is_given_up_on():
    async def slow(request: httpx2.Request) -> httpx2.Response:
        await asyncio.sleep(5)
        return httpx2.Response(200, json=GOOD)

    discovery = discover(slow, timeout=0.1)

    assert discovery.metadata is None
    assert discovery.tried[-1] == "gave up after 0.1 s"


def test_no_credential_is_ever_sent():
    server = Server({ROOT_8414: httpx2.Response(404), ROOT_OIDC: httpx2.Response(200, json=GOOD)})

    discover(server)

    assert server.requests
    assert all("authorization" not in request.headers for request in server.requests)
    assert all(request.method == "GET" for request in server.requests)


# --- auditing what a server announced --------------------------------------------------------


def audit(auth: AuthInfo, handler, **options) -> tuple[RuleResult, ...]:
    transport = httpx2.MockTransport(handler)
    return asyncio.run(
        audit_authorization_server(
            auth, RESOURCE, timeout=5.0, transport=transport, **options
        )
    )


def findings_of(results: tuple[RuleResult, ...]) -> list[Finding]:
    return [finding for result in results for finding in result.findings]


OAUTH_IDS = [rule.id for rule in OAUTH_RULES]


def test_nothing_is_checked_when_no_authorization_server_was_announced():
    server = Server({})

    results = audit(AuthInfo(challenge='Bearer realm="x"'), server)

    assert results == tuple(
        RuleResult.skipped(rule_id, NO_AUTHORIZATION_SERVER) for rule_id in OAUTH_IDS
    )
    assert server.requests == []


def test_a_sound_authorization_server_passes_every_check():
    server = Server({ROOT_8414: httpx2.Response(200, json=GOOD)})
    auth = AuthInfo(challenge=None, authorization_servers=(ANNOUNCED,))

    results = audit(auth, server)

    assert results == tuple(RuleResult(rule_id) for rule_id in OAUTH_IDS)
    assert all(result.outcome is Outcome.RAN for result in results)


def test_the_first_announced_authorization_server_is_the_one_checked():
    server = Server({ROOT_8414: httpx2.Response(200, json=GOOD)})
    auth = AuthInfo(challenge=None, authorization_servers=(ANNOUNCED, "https://other.test"))

    assert findings_of(audit(auth, server)) == []
    assert server.urls == [ROOT_8414]


def test_each_finding_is_in_the_result_of_its_own_rule():
    broken = changed(code_challenge_methods_supported=None, registration_endpoint=None)
    server = Server({ROOT_8414: httpx2.Response(200, json=broken)})
    auth = AuthInfo(challenge=None, authorization_servers=(ANNOUNCED,))

    results = {result.rule_id: result for result in audit(auth, server)}

    assert list(results) == OAUTH_IDS
    assert results["oauth.metadata"] == RuleResult("oauth.metadata")
    assert results["oauth.issuer"] == RuleResult("oauth.issuer")
    assert [f.rule_id for f in results["oauth.pkce"].findings] == ["oauth.pkce"]
    assert [f.rule_id for f in results["oauth.registration"].findings] == ["oauth.registration"]


def test_the_trailing_slash_setting_is_passed_through():
    server = Server({ROOT_8414: httpx2.Response(200, json=changed(issuer="https://as.test"))})
    auth = AuthInfo(challenge=None, authorization_servers=("https://as.test/",))

    [plain] = findings_of(audit(auth, server))
    [accepted] = findings_of(audit(auth, server, issuer_slash_accepted=True))

    assert "set `ignore_issuer_trailing_slash: true`" in plain.message
    assert "makes mcph accept" in accepted.message


def test_an_authorization_server_that_cannot_be_reached_is_reported_not_raised():
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused")

    auth = AuthInfo(challenge=None, authorization_servers=(ANNOUNCED,))

    results = audit(auth, refuse)

    [finding] = findings_of(results)
    assert finding.rule_id == "oauth.metadata"
    assert "ConnectError" in finding.message


def test_without_metadata_only_its_lookup_is_checked_and_the_other_checks_are_skipped():
    auth = AuthInfo(challenge=None, authorization_servers=(ANNOUNCED,))

    results = audit(auth, Server({}))  # every URL is a 404

    metadata, *others = results
    assert [f.rule_id for f in metadata.findings] == ["oauth.metadata"]
    assert metadata.outcome is Outcome.RAN
    assert others == [RuleResult.skipped(rule_id, NO_METADATA) for rule_id in OAUTH_IDS[1:]]
