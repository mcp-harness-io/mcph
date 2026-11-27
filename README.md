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

Describe your MCP servers in a `mcph.yaml` in your project:

```yaml
servers:
  - name: orders
    transport: stdio
    command: uv
    args: ["run", "python", "src/server.py"]
    env:
      LOG_LEVEL: WARNING
```

Then check them:

```console
$ mcph doctor
Checking 1 server from /path/to/mcph.yaml

orders
  OK     started in 423 ms (orders-server 1.0.0, MCP 2026-07-28), 3 tools
  WARN   core.tool_name_snake_case: tool 'getOrder' is not snake_case (try 'get_order')

All servers OK, 1 warning.
```

## Commands

| Command | What it does |
|---|---|
| `mcph doctor` | Starts each configured server, completes the MCP handshake, lists its tools, checks them against the built-in rules and reports problems. |
| `mcph version` | Prints the installed version. |
| `mcph proxy`, `mcph eval` | Placeholders, not implemented yet. |

- `-c, --config PATH` selects the config file (default `./mcph.yaml`). It goes before the command: `mcph -c other.yaml doctor`.
- `mcph doctor --timeout SECONDS` limits how long each server may take to start and answer (default 30).
- `mcph doctor --strict` also fails on warnings, which is handy in CI.
- `mcph doctor` exits with `0` if all servers are OK, `1` if a server failed (or, with `--strict`, if there are warnings), `2` if the config is invalid.

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
  - `transport`: `stdio` (the only transport so far).
  - `command`, `args`, `env`: how to start the server.
  - `primary`: optional `true` for the server you are developing (at most one).

Servers run from the directory containing `mcph.yaml`, with a minimal environment (`HOME`, `PATH`, ...) plus the `env` you list. Unknown keys are rejected, so typos are reported.
