# mcph

[![Coverage](https://raw.githubusercontent.com/mcp-harness-io/mcph/python-coverage-comment-action-data/badge.svg)](https://github.com/mcp-harness-io/mcph/tree/python-coverage-comment-action-data)

MCP Harness: diagnostics, real-client proxy and LLM-as-a-judge evals for MCP servers.

> Early development: only `mcph doctor` does real work so far.

## Install

```bash
pip install mcph    # Python 3.12+
uvx mcph --help     # or run it without installing
```

## Quick start

Describe your MCP servers in a `mcph.yaml` in your project: `stdio` servers that mcph starts for you, and `http` servers that are already running.

```yaml
servers:
  - name: orders
    transport: stdio
    command: uv
    args: ["run", "python", "src/server.py"]
    env:
      LOG_LEVEL: WARNING
  - name: docs
    transport: http
    url: http://localhost:8080/mcp
    auth:                      # optional: send a bearer token
      type: bearer
      token_env: DOCS_TOKEN    # name of an environment variable holding the token
```

Then check them:

```console
$ mcph doctor
Checking 2 servers from /path/to/mcph.yaml

orders
  OK     started in 423 ms (orders-server 1.0.0, MCP 2026-07-28), 3 tools
  WARN   core.tool_name_snake_case: tool 'getOrder' is not snake_case (try 'get_order')
docs
  OK     connected in 87 ms (docs-server 2.1.0, MCP 2026-07-28), 5 tools

All servers OK, 1 warning.
```

## Commands

| Command | What it does |
|---|---|
| `mcph doctor` | Starts each `stdio` server (or connects to an `http` one), completes the MCP handshake, lists its tools, checks them against the built-in rules and reports problems. |
| `mcph version` | Prints the installed version. |
| `mcph proxy`, `mcph eval` | Placeholders, not implemented yet. |

- `-c, --config PATH` selects the config file (default `./mcph.yaml`). It goes before the command: `mcph -c other.yaml doctor`.
- `mcph doctor --timeout SECONDS` limits how long each server may take to start and answer (default 30).
- `mcph doctor --strict` also fails on warnings, which is handy in CI.
- `mcph doctor` exits with `0` if all servers are OK, `1` if a server failed (or, with `--strict`, if there are warnings), `2` if the config is invalid.

If an `http` server refuses mcph, doctor says why instead of a generic error: no token was sent (401), the token was rejected (with the server's `error`, its explanation and the scope it asks for), or the token lacks a scope (403). It also shows what the server announces about its OAuth setup when it publishes protected resource metadata (RFC 9728): its authorization server and scopes.

```console
docs
  ERROR  authentication required: the server answered 401 (send a bearer token with `auth` in mcph.yaml)
         challenge: Bearer resource_metadata="http://localhost:8080/.well-known/oauth-protected-resource/mcp"
         authorization server: https://auth.example.com/
         scopes: api.read, api.write
```

## Rules

`mcph doctor` checks every server that starts against these built-in rules:

| Rule | Severity | Checks |
|---|---|---|
| `core.tool_name_snake_case` | warning | Tool names are snake_case, like `get_order` rather than `getOrder`. |

Warnings do not fail the run unless you pass `--strict`.

## mcph.yaml

- `version`: optional, `"1"`.
- `servers`: a list of servers, each with:
  - `name`: unique; letters, digits, `_` and `-`.
  - `transport`: `stdio` or `http`.
  - `command`, `args`, `env` (`stdio`): how to start the server.
  - `url` (`http`): the Streamable HTTP endpoint of a server you have already started, such as `http://localhost:8080/mcp`. mcph does not start it.
  - `auth` (`http`, optional): `type: bearer` and `token_env`, the name of an environment variable that holds a bearer token. mcph reads the token from its own environment, never from the YAML, and sends it as `Authorization: Bearer ...` to the server's URL only; it is never printed. OAuth login is not supported yet.
  - `primary`: optional `true` for the server you are developing (at most one).

`stdio` servers run from the directory containing `mcph.yaml`, with a minimal environment (`HOME`, `PATH`, ...) plus the `env` you list. Unknown keys are rejected, so typos are reported.
