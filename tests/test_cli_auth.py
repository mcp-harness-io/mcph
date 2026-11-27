import asyncio
import re
import time

import pytest
from fastmcp.client.auth.oauth import TokenStorageAdapter
from mcp.shared.auth import OAuthToken
from typer.testing import CliRunner

from mcph import cli
from mcph.cli import app
from mcph.config import load_config
from mcph.oauth_login import LoginError
from mcph.token_store import StoredToken, TokenStore, TokenStoreError

runner = CliRunner()

CONFIG = """\
servers:
  - name: docs
    transport: http
    url: http://localhost:8080/mcp
    auth: {type: oauth, scopes: [api.read]}
  - name: knowledge-base
    transport: http
    url: http://localhost:9090/mcp
    auth: {type: oauth}
  - name: plain
    transport: http
    url: http://localhost:7070/mcp
  - name: local
    transport: stdio
    command: uv
"""

ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture
def config_path(tmp_path):
    path = tmp_path / "mcph.yaml"
    path.write_text(CONFIG)
    return path


@pytest.fixture
def run(config_path):
    def invoke(*args: str):
        return runner.invoke(app, ["-c", str(config_path), *args])

    return invoke


def server(config_path, name):
    return next(s for s in load_config(config_path).servers if s.name == name)


def store_token(config_path, name="docs", **fields) -> None:
    target = server(config_path, name)
    adapter = TokenStorageAdapter(TokenStore().open(target), target.url.rstrip("/"))
    fields.setdefault("access_token", "abc123")
    asyncio.run(adapter.set_tokens(OAuthToken(token_type="Bearer", **fields)))


# --- help ---------------------------------------------------------------------------------


def test_auth_without_a_command_shows_its_commands():
    result = runner.invoke(app, ["auth"])

    text = ANSI.sub("", result.output)
    assert result.exit_code in (0, 2)  # depends on the installed click version
    assert all(command in text for command in ("login", "status", "logout"))


# --- status -------------------------------------------------------------------------------


def test_status_lists_the_oauth_servers_only(run):
    result = run("auth", "status")

    assert result.exit_code == 0, result.output
    assert re.search(r"^docs +not logged in$", result.output, re.M)
    assert re.search(r"^knowledge-base +not logged in$", result.output, re.M)
    assert "plain" not in result.output and "local" not in result.output


def test_status_shows_who_is_logged_in_and_when_the_token_expires(run, config_path):
    store_token(config_path, expires_in=900)

    result = run("auth", "status")

    assert re.search(r"^docs +logged in; the token expires in 1[45] min$", result.output, re.M)
    assert re.search(r"^knowledge-base +not logged in$", result.output, re.M)
    assert "abc123" not in result.output


def test_status_shows_an_expired_token(run, config_path):
    store_token(config_path, expires_in=1)
    time.sleep(1.2)

    result = run("auth", "status")

    assert re.search(r"^docs +logged in; the token expired \d+ s ago$", result.output, re.M)


def test_status_can_be_limited_to_one_server(run):
    result = run("auth", "status", "docs")

    assert result.exit_code == 0, result.output
    assert "docs" in result.output and "knowledge-base" not in result.output


def test_status_reports_an_unreadable_token(run, config_path):
    store_token(config_path, expires_in=900)
    directory = TokenStore().directory(server(config_path, "docs"))
    [token_file] = [p for p in directory.rglob("*_tokens-*.json") if p.is_file()]
    token_file.write_text("garbage")

    result = run("auth", "status", "docs")

    assert result.exit_code == 0, result.output
    assert re.search(r"^docs +unreadable: cannot read the stored token", result.output, re.M)


def test_status_without_any_oauth_server_says_so(tmp_path):
    path = tmp_path / "plain.yaml"
    path.write_text("servers:\n  - {name: local, transport: stdio, command: uv}\n")

    result = runner.invoke(app, ["-c", str(path), "auth", "status"])

    assert result.exit_code == 0, result.output
    assert "No server in the config uses `auth: {type: oauth}`." in result.output


# --- logout -------------------------------------------------------------------------------


def test_logout_deletes_the_token(run, config_path):
    store_token(config_path, expires_in=900)

    first = run("auth", "logout", "docs")
    second = run("auth", "logout", "docs")

    assert "Logged out of docs." in first.output
    assert "docs was not logged in." in second.output
    assert "docs  not logged in" in run("auth", "status", "docs").output


# --- login --------------------------------------------------------------------------------


@pytest.fixture
def fake_sign_in(monkeypatch):
    """Replace the browser flow; records how it was called."""
    calls = []
    outcome = {"result": StoredToken("secret", expires_at=time.time() + 900)}

    async def sign_in(target, store, *, announce, timeout):
        calls.append({"server": target.name, "store": store, "timeout": timeout})
        announce("https://auth.example.com/authorize?state=xyz")
        result = outcome["result"]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(cli, "sign_in", sign_in)
    return calls, outcome


def test_login_announces_the_url_and_reports_the_expiry(run, fake_sign_in):
    calls, _ = fake_sign_in

    result = run("auth", "login", "docs")

    assert result.exit_code == 0, result.output
    assert "Signing in to docs (http://localhost:8080/mcp)" in result.output
    assert (
        "Opening your browser. If it does not open, visit:\n"
        "  https://auth.example.com/authorize?state=xyz"
    ) in result.output
    assert re.search(r"Logged in to docs; the token expires in 1[45] min\.", result.output)
    assert "secret" not in result.output
    assert [(c["server"], c["timeout"]) for c in calls] == [("docs", 300.0)]


def test_login_passes_the_timeout_on(run, fake_sign_in):
    calls, _ = fake_sign_in

    result = run("auth", "login", "docs", "--timeout", "45")

    assert result.exit_code == 0, result.output
    assert calls[0]["timeout"] == 45.0


def test_login_rejects_a_silly_timeout(run, fake_sign_in):
    result = run("auth", "login", "docs", "--timeout", "0.2")

    assert result.exit_code == 2


@pytest.mark.parametrize(
    "error", [LoginError("the sign-in was refused"), TokenStoreError("disk is full")]
)
def test_a_failed_login_is_reported_and_fails_the_command(run, fake_sign_in, error):
    _, outcome = fake_sign_in
    outcome["result"] = error

    result = run("auth", "login", "docs")

    assert result.exit_code == 1
    assert f"Error: {error}" in result.output
    assert "Logged in" not in result.output


# --- picking the server -------------------------------------------------------------------


@pytest.mark.parametrize("command", ["login", "logout", "status"])
def test_an_unknown_server_is_reported_with_the_known_ones(run, command):
    result = run("auth", command, "nope")

    assert result.exit_code == 2
    assert "no server named 'nope'" in result.output
    assert "(known: docs, knowledge-base, plain, local)" in result.output


@pytest.mark.parametrize("name", ["plain", "local"])
@pytest.mark.parametrize("command", ["login", "logout", "status"])
def test_a_server_without_oauth_login_is_refused(run, command, name):
    result = run("auth", command, name)

    assert result.exit_code == 2
    assert f"server {name!r} does not use OAuth login" in result.output
    assert "transport: http" in result.output


@pytest.mark.parametrize("command", ["login", "logout", "status"])
def test_a_missing_config_is_reported(tmp_path, command):
    args = ["-c", str(tmp_path / "nope.yaml"), "auth", command]
    result = runner.invoke(app, args if command == "status" else [*args, "docs"])

    assert result.exit_code == 2
    assert "Config file not found" in result.output
