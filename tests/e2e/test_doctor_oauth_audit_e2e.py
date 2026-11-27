"""End-to-end tests: ``mcph doctor`` checking the OAuth setup a server announces."""

import re

import pytest

pytestmark = pytest.mark.e2e


def check(project, url, oauth=False):
    """Run doctor on a server; ``oauth`` (True or a dict of settings) makes it an OAuth one."""
    project.write_config(project.http_entry("docs", url, oauth=oauth))
    return project.doctor()


def findings(output: str) -> list[str]:
    return re.findall(r"^  (?:ERROR|WARN) +(oauth\.\S+): ", output, re.M)


def test_a_sound_setup_has_no_findings(project, fake_oauth_server):
    result = check(project, fake_oauth_server())

    assert result.exit_code == 1, result.output  # no auth configured: it still needs one
    assert "authentication required: the server answered 401" in result.output
    assert findings(result.output) == []
    assert "1 of 1 server failed." in result.output


def test_a_trailing_slash_difference_is_a_warning_that_names_the_setting(
    project, fake_oauth_server
):
    # Announce the authorization server with a slash; its metadata says it has none.
    url = fake_oauth_server(announced="{base}/")
    base = url.removesuffix("/mcp")

    result = check(project, url)

    assert findings(result.output) == ["oauth.issuer"]
    assert (
        f"WARN   oauth.issuer: the resource announces its authorization server as "
        f"{base}/ but that server's metadata says {base}; they differ only by a "
        "trailing slash, which strict clients refuse (RFC 8414); set "
        "`ignore_issuer_trailing_slash: true` under `auth` to have mcph accept it"
    ) in result.output
    assert "1 of 1 server failed, 1 warning." in result.output


def test_a_server_that_already_accepts_the_difference_is_told_so(project, fake_oauth_server):
    url = fake_oauth_server(announced="{base}/")

    result = check(project, url, oauth={"ignore_issuer_trailing_slash": True})

    assert "not logged in (run `mcph auth login docs`)" in result.output
    assert "`ignore_issuer_trailing_slash` makes mcph accept" in result.output


def test_a_different_issuer_is_an_error(project, fake_oauth_server):
    url = fake_oauth_server(issuer="https://other.example")

    result = check(project, url)

    assert findings(result.output) == ["oauth.issuer"]
    assert re.search(
        r"ERROR  oauth\.issuer: .* RFC 8414 requires them to be identical, so clients "
        r"refuse to sign in",
        result.output,
    )


def test_a_missing_registration_endpoint_is_a_warning(project, fake_oauth_server):
    result = check(project, fake_oauth_server(registration_endpoint=None))

    assert findings(result.output) == ["oauth.registration"]
    assert "WARN   oauth.registration: the authorization server offers no" in result.output


@pytest.mark.parametrize("methods", [None, ["plain"]])
def test_pkce_without_s256_is_an_error(project, fake_oauth_server, methods):
    result = check(project, fake_oauth_server(code_challenge_methods_supported=methods))

    assert findings(result.output) == ["oauth.pkce"]
    assert "ERROR  oauth.pkce: the authorization server does not advertise PKCE with S256" in (
        result.output
    )


def test_missing_metadata_lists_what_was_tried(project, fake_oauth_server):
    url = fake_oauth_server(missing=True)
    base = url.removesuffix("/mcp")

    result = check(project, url)

    assert findings(result.output) == ["oauth.metadata"]
    assert (
        f"cannot find the metadata of the authorization server {base} (tried: "
        f"{base}/.well-known/oauth-authorization-server (404); "
        f"{base}/.well-known/openid-configuration (404))"
    ) in result.output


def test_the_setup_is_checked_before_anyone_has_logged_in(project, fake_oauth_server):
    url = fake_oauth_server(announced="{base}/")

    result = check(project, url, oauth=True)  # an OAuth server, but never logged in

    assert "ERROR  not logged in (run `mcph auth login docs`)" in result.output
    assert findings(result.output) == ["oauth.issuer"]


def test_a_real_oauth_server_has_no_findings(project, start_http_server):
    url = start_http_server(auth="oauth")

    result = check(project, url, oauth=True)

    assert "not logged in (run `mcph auth login docs`)" in result.output
    assert findings(result.output) == []


def test_a_server_that_needs_no_authentication_is_not_audited(project, start_http_server):
    result = check(project, start_http_server())  # no OAuth involved

    assert result.exit_code == 0, result.output
    assert findings(result.output) == []
    assert "All servers OK." in result.output


def test_the_other_servers_are_checked_as_usual(project, fake_oauth_server):
    bad = fake_oauth_server(code_challenge_methods_supported=None)
    project.write_config(project.http_entry("docs", bad), project.demo_server("local"))

    result = project.doctor()

    assert result.exit_code == 1, result.output
    assert findings(result.output) == ["oauth.pkce"]
    assert re.search(r"local\n +OK +started in", result.output)
