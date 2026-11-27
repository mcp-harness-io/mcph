"""End-to-end tests: ``mcph auth login`` and ``doctor`` against a local OAuth server.

The server is a complete OAuth authorization and resource server (FastMCP's
in-memory provider, running as a subprocess), and the browser is simulated.
"""

import asyncio
import re
import time

import pytest
from fastmcp.client.auth.oauth import TokenStorageAdapter
from mcp.shared.auth import OAuthToken

from mcph.config import load_config
from mcph.token_store import TokenStore

pytestmark = pytest.mark.e2e


@pytest.fixture
def oauth_url(project, start_http_server):
    url = start_http_server(auth="oauth")
    project.write_config(project.http_entry("docs", url, oauth=True))
    return url


def stored_token_files(project) -> list:
    server = load_config(project.config_path).servers[0]
    directory = TokenStore().directory(server)
    return [path for path in directory.rglob("*") if path.is_file()]


def store_token(project, **fields) -> None:
    """Write a token for ``docs`` into the token store, as a login would."""
    server = load_config(project.config_path).servers[0]
    adapter = TokenStorageAdapter(TokenStore().open(server), server.url.rstrip("/"))
    token = OAuthToken(access_token="made-up-token", token_type="Bearer", **fields)
    asyncio.run(adapter.set_tokens(token))


def test_login_status_doctor_and_logout(project, oauth_url, browser):
    login = project.mcph("auth", "login", "docs")

    assert login.exit_code == 0, login.output
    assert f"Signing in to docs ({oauth_url})" in login.output
    assert "Opening your browser. If it does not open, visit:" in login.output
    assert re.search(r"Logged in to docs; the token expires in \d+ min\.", login.output)
    assert len(browser) == 1 and browser[0] in login.output  # the URL was announced

    status = project.mcph("auth", "status")
    assert status.exit_code == 0, status.output
    assert re.search(r"docs  logged in; the token expires in \d+ min", status.output)

    doctor = project.doctor()
    assert doctor.exit_code == 0, doctor.output
    assert re.search(r"docs\n +OK +connected in \d+ ms .*, 2 tools", doctor.output)

    logout = project.mcph("auth", "logout", "docs")
    assert logout.exit_code == 0, logout.output
    assert "Logged out of docs." in logout.output
    assert stored_token_files(project) == []
    assert "docs  not logged in" in project.mcph("auth", "status").output
    assert "was not logged in" in project.mcph("auth", "logout", "docs").output


def test_login_is_quiet_apart_from_what_the_user_needs(project, oauth_url, browser):
    login = project.mcph("auth", "login", "docs")

    assert login.exit_code == 0, login.output
    assert "callback server" not in login.output
    assert "OAuth authorization URL" not in login.output


def test_the_token_is_never_printed(project, oauth_url, browser):
    project.mcph("auth", "login", "docs")
    server = load_config(project.config_path).servers[0]
    stored = asyncio.run(TokenStore().load(server))
    outputs = [
        project.mcph("auth", "status").output,
        project.doctor().output,
        project.mcph("auth", "logout", "docs").output,
    ]

    assert stored is not None
    assert all(stored.access_token not in output for output in outputs)


def test_only_the_access_token_is_kept_and_only_the_user_can_read_it(
    project, oauth_url, browser
):
    project.mcph("auth", "login", "docs")
    server = load_config(project.config_path).servers[0]
    store = TokenStore()
    adapter = TokenStorageAdapter(store.open(server), server.url.rstrip("/"))

    tokens = asyncio.run(adapter.get_tokens())

    assert tokens is not None and tokens.access_token
    assert tokens.refresh_token is None  # mcph cannot use it, so it does not keep it
    for path in [store.directory(server), *stored_token_files(project)]:
        assert path.stat().st_mode & 0o077 == 0, f"{path} is readable by others"


def test_logging_in_again_replaces_the_token(project, oauth_url, browser):
    project.mcph("auth", "login", "docs")
    server = load_config(project.config_path).servers[0]
    first = asyncio.run(TokenStore().load(server)).access_token

    again = project.mcph("auth", "login", "docs")

    assert again.exit_code == 0, again.output
    assert len(browser) == 2  # a fresh sign-in, not a reuse of the old token
    assert asyncio.run(TokenStore().load(server)).access_token != first


def test_doctor_asks_for_a_login_when_there_is_none(project, oauth_url):
    result = project.doctor()

    assert result.exit_code == 1, result.output
    assert "ERROR  not logged in (run `mcph auth login docs`)" in result.output
    assert "docs  not logged in" in project.mcph("auth", "status").output


def test_doctor_says_when_the_stored_token_has_expired(project, oauth_url):
    store_token(project, expires_in=1)
    time.sleep(1.5)

    result = project.doctor()

    assert result.exit_code == 1, result.output
    assert re.search(
        r"ERROR  the stored token expired \d+ s ago \(run `mcph auth login docs`\)",
        result.output,
    )
    assert "made-up-token" not in result.output
    assert re.search(r"docs  logged in; the token expired", project.mcph("auth", "status").output)


def test_doctor_explains_a_token_the_server_does_not_accept(project, oauth_url):
    store_token(project, expires_in=900)

    result = project.doctor()

    assert result.exit_code == 1, result.output
    assert (
        "ERROR  the server rejected the stored token (HTTP 401); "
        "run `mcph auth login docs` again"
    ) in result.output
    assert "made-up-token" not in result.output


def test_login_to_a_server_that_does_not_need_it_stores_nothing(
    project, start_http_server, browser
):
    url = start_http_server()  # no authentication
    project.write_config(project.http_entry("docs", url, oauth=True))

    result = project.mcph("auth", "login", "docs")

    assert result.exit_code == 1, result.output
    assert "did not ask for authentication" in result.output
    assert browser == []
    assert stored_token_files(project) == []


def test_login_gives_up_when_nobody_signs_in(project, oauth_url, idle_browser):
    result = project.mcph("auth", "login", "docs", "--timeout", "2")

    assert result.exit_code == 1, result.output
    assert "Error: timed out after 2 s waiting for the sign-in to finish" in result.output
    assert len(idle_browser) == 1
    assert stored_token_files(project) == []


def test_login_reports_a_server_that_is_not_there(project, closed_port_url, browser):
    project.write_config(project.http_entry("docs", closed_port_url, oauth=True))

    result = project.mcph("auth", "login", "docs")

    assert result.exit_code == 1, result.output
    assert "Error: " in result.output
    assert browser == []
    assert stored_token_files(project) == []


def test_tokens_are_kept_per_server_entry(project, start_http_server, browser):
    first = start_http_server(auth="oauth")
    second = start_http_server(auth="oauth")
    project.write_config(
        project.http_entry("one", first, oauth=True),
        project.http_entry("two", second, oauth=True),
    )

    assert project.mcph("auth", "login", "one").exit_code == 0

    status = project.mcph("auth", "status").output
    assert re.search(r"one  logged in", status)
    assert re.search(r"two  not logged in", status)
    assert project.doctor().exit_code == 1  # "two" has no token
