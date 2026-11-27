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
extends:
  - builtin:core               # the rules that ship with mcph (without a pack there is no rule)

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
$ mcph doctor run
Checking 2 servers from /path/to/mcph.yaml

orders
  OK     started in 423 ms (orders-server 1.0.0, MCP 2026-07-28), 3 tools
  WARN   core.tool_name_snake_case: tool 'getOrder' is not snake_case (try 'get_order')
docs
  OK     connected in 87 ms (docs-server 2.1.0, MCP 2026-07-28), 5 tools
  OK     core.tool_name_snake_case
  SKIP   oauth.metadata, oauth.issuer, oauth.pkce, oauth.registration: not needed, the server accepted the connection

All servers OK, 1 warning.
```

`extends` lists the packs whose rules doctor uses; `builtin:core` is the pack that ships with mcph (see [Rules and packs](#rules-and-packs)). Under each server doctor lists every rule: `OK` ran and found nothing, `WARN` or `ERROR` found a problem, `OFF` is turned off in `mcph.yaml`, and `SKIP` could not run or was not needed (skipped rules with the same reason share one line). To make a rule fail the run instead, or to switch it off, add a `doctor` section to the same file:

```yaml
doctor:
  rules:
    core.tool_name_snake_case: error   # or warn, or off
```

See [Configuring rules](#configuring-rules) for the details.

## Commands

| Command | What it does |
|---|---|
| `mcph doctor run` | Starts each `stdio` server (or connects to an `http` one), completes the MCP handshake, lists its tools, checks them against the rules of the packs under `extends` and reports what each rule found. |
| `mcph doctor rules` | Lists the rules of the packs under `extends` with their severity: the default, or the setting from `doctor.rules` in `mcph.yaml`. It starts no server. |
| `mcph auth login`, `status`, `logout` | Signs in to an `http` server that uses OAuth through the browser, shows who is logged in, and signs out again. |
| `mcph version` | Prints the installed version. |
| `mcph proxy`, `mcph eval` | Placeholders, not implemented yet: `proxy` is planned for 0.3.0 and `eval` for 0.4.0. |

`mcph doctor` is a group of commands: on its own it is an error, so a script that still calls it fails instead of passing without checking anything. `mcph doctor --help` lists its commands.

- `-c, --config PATH` selects the config file (default `./mcph.yaml`). It goes before the command: `mcph -c other.yaml doctor run`.
- `mcph doctor run --timeout SECONDS` limits how long each server may take to start and answer (default 30).
- `mcph doctor run --strict` also fails on warnings, which is handy in CI.
- `mcph doctor run` exits with `0` if all servers are OK, `1` if a server failed (or, with `--strict`, if there are warnings), `2` if the config is invalid.

If an `http` server refuses mcph, doctor says why instead of a generic error: no token was sent (401), the token was rejected (with the server's `error`, its explanation and the scope it asks for), or the token lacks a scope (403). It also shows what the server announces about its OAuth setup when it publishes protected resource metadata (RFC 9728): its authorization server and scopes.

```console
docs
  ERROR  authentication required: the server answered 401 (add `auth` to this server in mcph.yaml)
         challenge: Bearer resource_metadata="http://localhost:8080/.well-known/oauth-protected-resource/mcp"
         authorization server: https://auth.example.com/
         scopes: api.read, api.write
  SKIP   core.tool_name_snake_case: mcph could not connect to the server
  OK     oauth.metadata
  OK     oauth.issuer
  OK     oauth.pkce
  OK     oauth.registration
```

Doctor also checks the OAuth setup the server announces (for an `auth: {type: oauth}` server that is not logged in yet too), so a problem that would stop a client from signing in shows up before anyone tries. It only sends unauthenticated requests, and only for a server that needs authentication. For a server that connected, these checks are listed as `SKIP`:

| Check | Severity | Looks for |
|---|---|---|
| `oauth.metadata` | error | The authorization server's metadata can be found and has an authorization and a token endpoint. |
| `oauth.issuer` | error (warning if only a trailing slash differs) | The metadata's `issuer` is the authorization server the resource announced. |
| `oauth.pkce` | error | PKCE with `S256` is advertised. |
| `oauth.registration` | warning | Dynamic client registration is offered. |

These rules are in the `core` pack, and you can configure each of them (see [Rules and packs](#rules-and-packs) and [Configuring rules](#configuring-rules)).

## OAuth login

For a server that uses OAuth, set `auth: {type: oauth}` (optionally with `scopes`) and sign in once:

```console
$ mcph auth login docs
Signing in to docs (http://localhost:8080/mcp)
Opening your browser. If it does not open, visit:
  https://auth.example.com/authorize?response_type=code&client_id=...
Logged in to docs; the token expires in 15 min.
```

`mcph doctor run` then uses the stored token. It is an access token that usually expires after a few minutes; when it has, doctor tells you to log in again. `mcph auth status` shows who is logged in and when each token expires, and `mcph auth logout docs` deletes the token. For CI use a bearer token from an environment variable instead.

Tokens are kept in a file only you can read, under mcph's state directory (set `MCPH_STATE_DIR` to put it elsewhere). mcph does not keep refresh tokens.

If login stops with `Authorization server metadata issuer mismatch`, the server announces its authorization server differently from the way that server describes itself. The standard wants the two to be identical, so the right fix is on the server. When the only difference is a trailing slash (`https://auth.example.com/` against `https://auth.example.com`), you can have mcph accept it with `ignore_issuer_trailing_slash: true` under `auth`. It tolerates that one difference and nothing else.

## Rules and packs

Rules come from **packs**. A pack is a directory with a `mcph-pack.yaml` that lists the Python files defining its rules. Name the packs doctor should use under `extends` in `mcph.yaml`:

```yaml
extends:
  - builtin:core      # the rules that ship with mcph
  - ./packs/acme      # a pack of your own, relative to the directory of mcph.yaml
```

A source is a directory (relative to the directory of `mcph.yaml`, absolute, or starting with `~`) or `builtin:<name>` for a pack bundled with mcph. **Without any pack there is no rule**: `mcph doctor run` then only reaches the servers and warns about it, and the warning fails a `--strict` run.

### The core pack

`builtin:core` has the `oauth.*` checks described above and this rule:

| Rule | Severity | Checks |
|---|---|---|
| `core.tool_name_snake_case` | warning | Tool names are snake_case, like `get_order` rather than `getOrder`. |

Warnings do not fail the run unless you pass `--strict`.

### Writing a pack

```
packs/acme/
  mcph-pack.yaml
  rules/no_delete.py
```

```yaml
# mcph-pack.yaml
name: acme
version: "1.0.0"
description: Our checks.        # optional
rules:
  - rules/no_delete.py          # the files that define rules
```

```python
# rules/no_delete.py
from mcph.extensions import doctor_rule

@doctor_rule(id="acme.no_delete", description="No tool deletes data.", severity="error")
def no_delete(context):
    return [
        f"{tool.name} deletes data"
        for tool in context.snapshot.tools
        if tool.name.startswith("delete")
    ]
```

- A rule id looks like `namespace.name` (lowercase letters, digits and `_`, and `-` in the namespace) and is unique across all packs. Two packs with the same name are an error too.
- The function gets a context: `server` (the entry of `mcph.yaml`), `snapshot` (what the server exposes: `server_name`, `server_version`, `protocol_version`, `startup_seconds` and `tools`, each with a `name` and a `description`; `None` if mcph could not connect), `error` (why it could not), `timeout`, and a `cache` shared by the rules that check one server.
- It returns a message for each problem, at the rule's severity, or `Issue(message, severity)` objects (from `mcph.rules`) to choose the severity per problem. It may be `async`, and it may raise `SkipRule("why")` (from `mcph.rules`) when there is nothing to check.
- `@doctor_rule` also takes `transports=["http"]` for a rule that only applies to HTTP servers (the default is both kinds), and `needs_connection=False` for a rule that is still useful when the server could not be reached (without it, such a rule is skipped, because there is no snapshot).
- A rule that raises is reported as an error finding, and the other rules still run.
- A pack file is imported on its own, so it can import `mcph` and installed packages but not the other files of the pack.
- **The Python of a pack runs with your privileges**, like the `command` of a `stdio` server in the same file, so list only packs you trust.

### Configuring rules

Under `doctor.rules` in `mcph.yaml`, set how serious a rule is, or turn it off. This applies to the rules of every loaded pack, by rule id:

```yaml
doctor:
  rules:
    core.tool_name_snake_case: error   # a camelCase tool name now fails the run
    oauth.registration: off            # do not check for dynamic client registration
```

- `error`: findings of the rule are errors, so the run fails (exit code `1`).
- `warn`: findings are warnings, which fail the run only with `--strict`.
- `off`: the rule does not run, and the report lists it as `OFF`. Write it without quotes if you like: YAML reads `off` as false, and mcph accepts that.

Rules you do not list keep their default severity. A configured severity replaces the default for all findings of that rule, so `oauth.issuer: error` also makes a trailing-slash difference an error. With all four `oauth.*` checks off, doctor does not contact an authorization server at all.

A rule id that no loaded pack defines is a config error (exit code `2`) that lists the known ids and suggests the closest one. (Without `extends` no rule is known, and the message says how to add the core pack.)

`mcph doctor rules` lists the rules of the loaded packs, under the pack each comes from, with the setting that applies and the default next to one you changed. It starts no server:

```console
$ mcph doctor rules
Rules (settings from /path/to/mcph.yaml)

core (builtin:core)
  core.tool_name_snake_case  error (default: warn)  Tool names are snake_case.
  oauth.metadata             error                  The authorization server's metadata can be found and has an authorization and a token endpoint.
  oauth.issuer               error                  The metadata's issuer is the authorization server the resource announced (a difference of only a trailing slash is a warning).
  oauth.pkce                 error                  PKCE with S256 is advertised.
  oauth.registration         off (default: warn)    Dynamic client registration is offered.

acme (./packs/acme)
  acme.no_delete             error                  No tool deletes data.
```

Without a `mcph.yaml` in the current directory, or without `extends`, no pack is loaded and the listing says so. A `-c` path that does not exist, a pack that cannot be loaded, or an invalid config is an error (exit code `2`).

## mcph.yaml

- `version`: optional, `"1"`.
- `servers`: a list of servers, each with:
  - `name`: unique; letters, digits, `_` and `-`.
  - `transport`: `stdio` or `http`.
  - `command`, `args`, `env` (`stdio`): how to start the server.
  - `url` (`http`): the Streamable HTTP endpoint of a server you have already started, such as `http://localhost:8080/mcp`. mcph does not start it.
  - `auth` (`http`, optional), one of:
    - `type: bearer` and `token_env`, the name of an environment variable that holds a bearer token. mcph reads the token from its own environment, never from the YAML, and sends it as `Authorization: Bearer ...` to the server's URL only; it is never printed.
    - `type: oauth`, optional `scopes` and optional `ignore_issuer_trailing_slash`: sign in through the browser with `mcph auth login <server>` (see [OAuth login](#oauth-login)).
  - `primary`: optional `true` for the server you are developing (at most one).
- `extends`: optional list of the packs whose rules doctor uses: a directory or `builtin:<name>` (see [Rules and packs](#rules-and-packs)). Without any pack there is no rule; `builtin:core` has the rules that ship with mcph.
- `doctor`: optional settings for `mcph doctor`.
  - `rules`: how serious each rule is, as `rule id: error | warn | off` (see [Configuring rules](#configuring-rules)).

`stdio` servers run from the directory containing `mcph.yaml`, with a minimal environment (`HOME`, `PATH`, ...) plus the `env` you list. Unknown keys are rejected, so typos are reported.
