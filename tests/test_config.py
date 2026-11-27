import textwrap
from pathlib import Path

import pytest

from mcph.config import (
    BearerAuthConfig,
    Config,
    ConfigError,
    HttpServerConfig,
    OAuthAuthConfig,
    StdioServerConfig,
    load_config,
)


def write(tmp_path: Path, text: str, name: str = "mcph.yaml") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def load(tmp_path: Path, text: str) -> Config:
    return load_config(write(tmp_path, text))


def error_for(tmp_path: Path, text: str) -> str:
    with pytest.raises(ConfigError) as excinfo:
        load(tmp_path, text)
    return str(excinfo.value)


MINIMAL = """\
servers:
  - name: orders
    transport: stdio
    command: uv
"""


# --- valid configs ---------------------------------------------------------


def test_minimal_config_applies_defaults(tmp_path):
    config = load(tmp_path, MINIMAL)

    assert config.version == "1"
    [server] = config.servers
    assert server.name == "orders"
    assert server.transport == "stdio"
    assert server.command == "uv"
    assert server.args == []
    assert server.env == {}
    assert server.primary is False


def test_full_server_entry(tmp_path):
    config = load(
        tmp_path,
        """\
        version: "1"
        servers:
          - name: orders
            primary: true
            transport: stdio
            command: uv
            args: ["run", "python", "src/server.py"]
            env:
              LOG_LEVEL: "WARNING"
          - name: github
            transport: stdio
            command: npx
            args: ["-y", "@modelcontextprotocol/server-github"]
        """,
    )

    orders, github = config.servers
    assert orders.args == ["run", "python", "src/server.py"]
    assert orders.env == {"LOG_LEVEL": "WARNING"}
    assert orders.primary is True
    assert github.name == "github"


@pytest.mark.parametrize("version", ["1", '"1"'])
def test_version_accepts_int_and_string(tmp_path, version):
    config = load(tmp_path, f"version: {version}\n" + MINIMAL)

    assert config.version == "1"


@pytest.mark.parametrize("name", ["orders", "legacy_crm", "my-server", "2fa", "A1"])
def test_valid_server_names(tmp_path, name):
    config = load(tmp_path, MINIMAL.replace("orders", name))

    assert config.servers[0].name == name


def test_primary_server_prefers_the_marked_one(tmp_path):
    config = load(
        tmp_path,
        """\
        servers:
          - {name: first, transport: stdio, command: a}
          - {name: second, transport: stdio, command: b, primary: true}
        """,
    )

    assert config.primary_server.name == "second"


def test_primary_server_defaults_to_the_first(tmp_path):
    config = load(
        tmp_path,
        """\
        servers:
          - {name: first, transport: stdio, command: a}
          - {name: second, transport: stdio, command: b}
        """,
    )

    assert config.primary_server.name == "first"


# --- locating the file ------------------------------------------------------


def test_path_is_the_absolute_path_of_the_config_file(tmp_path):
    path = write(tmp_path, MINIMAL)

    assert load_config(path).path == path


def test_config_built_in_code_has_no_path():
    config = Config(
        servers=[StdioServerConfig(name="a", transport="stdio", command="x")]
    )

    assert config.path is None
    assert config.base_dir == Path.cwd()


def test_base_dir_is_the_directory_of_the_config_file(tmp_path):
    nested = tmp_path / "project"
    nested.mkdir()
    config = load_config(write(nested, MINIMAL))

    assert config.base_dir == nested


def test_base_dir_is_absolute_for_relative_paths(tmp_path, monkeypatch):
    write(tmp_path, MINIMAL, name="custom.yaml")
    monkeypatch.chdir(tmp_path)

    config = load_config("custom.yaml")

    assert config.base_dir == Path.cwd()
    assert config.base_dir.is_absolute()


def test_default_path_is_mcph_yaml_in_the_working_directory(tmp_path, monkeypatch):
    write(tmp_path, MINIMAL)
    monkeypatch.chdir(tmp_path)

    config = load_config()

    assert config.servers[0].name == "orders"
    assert config.base_dir == Path.cwd()


def test_missing_file(tmp_path):
    missing = tmp_path / "nope.yaml"

    with pytest.raises(ConfigError, match="Config file not found") as excinfo:
        load_config(missing)

    assert str(missing) in str(excinfo.value)


def test_unreadable_file(tmp_path):
    # A directory exists but cannot be read as a file.
    with pytest.raises(ConfigError, match="Cannot read config file"):
        load_config(tmp_path)


# --- invalid content --------------------------------------------------------


def test_invalid_yaml_reports_the_position(tmp_path):
    message = error_for(tmp_path, "servers: [unclosed\n")

    assert "Invalid YAML" in message
    assert "line" in message and "column" in message


def test_invalid_yaml_without_a_position(tmp_path):
    # Control characters make the YAML reader fail before it has a line/column.
    message = error_for(tmp_path, "servers: \x00\n")

    assert "Invalid YAML" in message
    assert "special characters are not allowed" in message


def test_empty_file_reports_missing_servers(tmp_path):
    message = error_for(tmp_path, "")

    assert "servers: Field required" in message


def test_top_level_must_be_a_mapping(tmp_path):
    message = error_for(tmp_path, "- a\n- b\n")

    assert "top level must be a mapping" in message
    assert "list" in message


@pytest.mark.parametrize("key", ["extends", "gateway", "doctor", "eval", "typo"])
def test_unknown_top_level_key_is_rejected(tmp_path, key):
    message = error_for(tmp_path, MINIMAL + f"{key}: {{}}\n")

    assert f"{key}: Extra inputs are not permitted" in message


def test_unknown_server_key_is_rejected(tmp_path):
    message = error_for(tmp_path, MINIMAL + "    auth: {type: bearer}\n")

    assert "servers[0].auth: Extra inputs are not permitted" in message


def test_http_server_entry(tmp_path):
    config = load(
        tmp_path,
        """\
        servers:
          - name: docs
            transport: http
            url: https://example.com/mcp
            primary: true
        """,
    )

    [server] = config.servers
    assert isinstance(server, HttpServerConfig)
    assert server.url == "https://example.com/mcp"
    assert server.primary is True


def test_stdio_and_http_servers_can_be_mixed(tmp_path):
    config = load(
        tmp_path,
        """\
        servers:
          - {name: local, transport: stdio, command: uv}
          - {name: remote, transport: http, url: "http://127.0.0.1:8080/mcp"}
        """,
    )

    assert [type(server) for server in config.servers] == [
        StdioServerConfig,
        HttpServerConfig,
    ]
    assert config.primary_server.name == "local"


def test_server_names_must_be_unique_across_transports(tmp_path):
    message = error_for(
        tmp_path,
        """\
        servers:
          - {name: a, transport: stdio, command: uv}
          - {name: a, transport: http, url: "http://x/mcp"}
        """,
    )

    assert "duplicate server name 'a'" in message


def test_http_server_requires_a_url(tmp_path):
    message = error_for(tmp_path, "servers:\n  - {name: docs, transport: http}\n")

    assert "servers[0].url: Field required" in message
    assert ".http." not in message  # pydantic's union tag is not a YAML key


@pytest.mark.parametrize(
    "url", ["ftp://example.com/mcp", "localhost:8080/mcp", "/mcp", "example.com", ""]
)
def test_http_url_must_be_http_or_https(tmp_path, url):
    message = error_for(
        tmp_path,
        f"servers:\n  - {{name: docs, transport: http, url: '{url}'}}\n",
    )

    assert "servers[0].url: must be an http:// or https:// URL" in message


@pytest.mark.parametrize("key", ["command", "args", "env"])
def test_http_server_rejects_stdio_only_keys(tmp_path, key):
    value = "x" if key == "command" else "[]" if key == "args" else "{}"
    message = error_for(
        tmp_path,
        "servers:\n"
        f"  - {{name: docs, transport: http, url: 'http://x/mcp', {key}: {value}}}\n",
    )

    assert f"servers[0].{key}: Extra inputs are not permitted" in message


def test_stdio_server_rejects_a_url(tmp_path):
    message = error_for(tmp_path, MINIMAL + "    url: http://x/mcp\n")

    assert "servers[0].url: Extra inputs are not permitted" in message


def test_stdio_server_still_requires_a_command(tmp_path):
    message = error_for(tmp_path, "servers:\n  - {name: a, transport: stdio}\n")

    assert "servers[0].command: Field required" in message
    assert ".stdio." not in message


def test_transport_is_required(tmp_path):
    message = error_for(tmp_path, "servers:\n  - {name: orders, command: uv}\n")

    assert "servers[0].transport: Field required (stdio or http)" in message


def test_unknown_transport_is_rejected(tmp_path):
    message = error_for(
        tmp_path, "servers:\n  - {name: a, transport: sse, url: 'http://x/sse'}\n"
    )

    assert "servers[0].transport: Input should be 'stdio' or 'http'" in message


def test_unsupported_version_is_rejected(tmp_path):
    message = error_for(tmp_path, 'version: "2"\n' + MINIMAL)

    assert "version: Input should be '1'" in message


def test_servers_must_not_be_empty(tmp_path):
    message = error_for(tmp_path, "servers: []\n")

    assert "servers: List should have at least 1 item" in message


def test_duplicate_server_names_are_rejected(tmp_path):
    message = error_for(
        tmp_path,
        """\
        servers:
          - {name: orders, transport: stdio, command: a}
          - {name: orders, transport: stdio, command: b}
        """,
    )

    assert "servers: duplicate server name 'orders'" in message


@pytest.mark.parametrize("name", ["bad name", "-leading", "_leading", "a/b", ""])
def test_invalid_server_names_are_rejected(tmp_path, name):
    message = error_for(tmp_path, MINIMAL.replace("orders", f'"{name}"'))

    assert "servers[0].name:" in message


def test_only_one_primary_server_is_allowed(tmp_path):
    message = error_for(
        tmp_path,
        """\
        servers:
          - {name: a, transport: stdio, command: x, primary: true}
          - {name: b, transport: stdio, command: y, primary: true}
        """,
    )

    assert "only one server may set 'primary: true', found: a, b" in message


def test_command_must_not_be_empty(tmp_path):
    message = error_for(tmp_path, MINIMAL.replace("command: uv", 'command: ""'))

    assert "servers[0].command:" in message


def test_args_must_be_strings(tmp_path):
    message = error_for(tmp_path, MINIMAL + "    args: [8080]\n")

    assert "servers[0].args[0]: Input should be a valid string" in message


def test_env_values_must_be_strings(tmp_path):
    message = error_for(tmp_path, MINIMAL + "    env: {PORT: 8080}\n")

    assert "servers[0].env.PORT:" in message


def test_all_problems_are_reported_together(tmp_path):
    message = error_for(
        tmp_path,
        """\
        extends: []
        servers:
          - {name: "bad name", transport: stdio, command: ""}
        """,
    )

    assert "extends: Extra inputs are not permitted" in message
    assert "servers[0].name:" in message
    assert "servers[0].command:" in message


# --- auth -------------------------------------------------------------------------

HTTP_WITH_AUTH = """\
servers:
  - name: docs
    transport: http
    url: https://example.com/mcp
    auth:
      type: bearer
      token_env: DOCS_TOKEN
"""


def test_http_server_can_send_a_bearer_token_from_an_environment_variable(tmp_path):
    config = load(tmp_path, HTTP_WITH_AUTH)

    [server] = config.servers
    assert server.auth == BearerAuthConfig(type="bearer", token_env="DOCS_TOKEN")


def test_auth_is_optional(tmp_path):
    config = load(
        tmp_path,
        "servers:\n  - {name: docs, transport: http, url: 'http://x/mcp'}\n",
    )

    assert config.servers[0].auth is None


def test_a_token_cannot_be_written_into_the_config(tmp_path):
    message = error_for(tmp_path, HTTP_WITH_AUTH + "      token: abc123\n")

    assert "servers[0].auth.token: Extra inputs are not permitted" in message


def test_auth_needs_a_type(tmp_path):
    message = error_for(
        tmp_path,
        "servers:\n  - {name: d, transport: http, url: 'http://x/mcp', auth: {}}\n",
    )

    assert "servers[0].auth.type: Field required (bearer or oauth)" in message


def test_bearer_auth_needs_a_variable_name(tmp_path):
    message = error_for(
        tmp_path,
        "servers:\n"
        "  - {name: d, transport: http, url: 'http://x/mcp', auth: {type: bearer}}\n",
    )

    assert "servers[0].auth.token_env: Field required" in message
    assert ".bearer." not in message  # pydantic's union tag is not a YAML key


def test_unknown_auth_types_are_rejected(tmp_path):
    message = error_for(tmp_path, HTTP_WITH_AUTH.replace("type: bearer", "type: kerberos"))

    assert "servers[0].auth.type: Input should be 'bearer' or 'oauth'" in message


@pytest.mark.parametrize("name", ["1TOKEN", "MY-TOKEN", "my token", "$TOKEN", ""])
def test_token_env_must_be_a_variable_name(tmp_path, name):
    message = error_for(
        tmp_path, HTTP_WITH_AUTH.replace("token_env: DOCS_TOKEN", f"token_env: '{name}'")
    )

    assert "servers[0].auth.token_env: must be an environment variable name" in message


def test_stdio_servers_have_no_auth_setting(tmp_path):
    message = error_for(
        tmp_path, MINIMAL + "    auth: {type: bearer, token_env: TOKEN}\n"
    )

    assert "servers[0].auth: Extra inputs are not permitted" in message


# --- oauth ---------------------------------------------------------------------------------


def oauth_server(auth: str) -> str:
    return f"servers:\n  - {{name: d, transport: http, url: 'http://x/mcp', auth: {auth}}}\n"


def test_http_server_can_sign_in_with_oauth(tmp_path):
    config = load(tmp_path, oauth_server("{type: oauth}"))

    assert config.servers[0].auth == OAuthAuthConfig(type="oauth", scopes=[])


def test_oauth_can_ask_for_scopes(tmp_path):
    config = load(tmp_path, oauth_server("{type: oauth, scopes: [api.read, api.write]}"))

    assert config.servers[0].auth.scopes == ["api.read", "api.write"]


@pytest.mark.parametrize("scope", ["'a b'", "''", "' '", '"x\\ty"'])
def test_scopes_must_be_words(tmp_path, scope):
    message = error_for(tmp_path, oauth_server(f"{{type: oauth, scopes: [{scope}]}}"))

    assert "servers[0].auth.scopes: each scope must be a non-empty word" in message
    assert ".oauth." not in message  # pydantic's union tag is not a YAML key


def test_scopes_must_be_strings(tmp_path):
    message = error_for(tmp_path, oauth_server("{type: oauth, scopes: [5]}"))

    assert "servers[0].auth.scopes[0]: Input should be a valid string" in message


def test_oauth_does_not_take_a_token_variable(tmp_path):
    message = error_for(tmp_path, oauth_server("{type: oauth, token_env: TOKEN}"))

    assert "servers[0].auth.token_env: Extra inputs are not permitted" in message


def test_bearer_does_not_take_scopes(tmp_path):
    message = error_for(
        tmp_path, oauth_server("{type: bearer, token_env: TOKEN, scopes: [a]}")
    )

    assert "servers[0].auth.scopes: Extra inputs are not permitted" in message

