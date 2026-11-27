"""A small FastMCP server for the e2e tests; ``mcph doctor`` runs it as a subprocess.

Its behaviour is controlled through environment variables, which the tests set
in the ``env`` block of ``mcph.yaml`` (so that block is exercised too):

E2E_TOOLS          comma-separated tool names to expose (default: get_order,list_orders)
E2E_VERSION        server version to report (default: 9.9.9)
E2E_PID_FILE       file to write the server's process id to
E2E_FAIL_ON_START  if set, print an error to stderr and exit before serving
E2E_STARTUP_DELAY  seconds to sleep before serving
E2E_TRANSPORT      "http" to serve Streamable HTTP on 127.0.0.1:$E2E_PORT (default: stdio)
E2E_PORT           port for the http transport
E2E_AUTH           http only. "oauth": a complete local OAuth server (registration, sign-in
                   that approves at once, tokens). Any other value: require one of the
                   fixed bearer tokens like an OAuth-protected server;
                   valid tokens: e2e-secret-token (scope api.read) and
                   e2e-writer-token (scopes api.read and api.write)
E2E_REQUIRED_SCOPES  comma-separated scopes a token must have (with E2E_AUTH)
"""

import os
import sys
import time
from pathlib import Path

from fastmcp import FastMCP

pid_file = os.environ.get("E2E_PID_FILE")
if pid_file:
    Path(pid_file).write_text(str(os.getpid()))

if os.environ.get("E2E_FAIL_ON_START"):
    print("boom: the demo server refuses to start", file=sys.stderr)
    sys.exit(3)

time.sleep(float(os.environ.get("E2E_STARTUP_DELAY", "0")))

auth = None
if os.environ.get("E2E_AUTH") == "oauth":
    from fastmcp.server.auth.providers.in_memory import InMemoryOAuthProvider
    from mcp.server.auth.settings import ClientRegistrationOptions

    auth = InMemoryOAuthProvider(
        base_url=f"http://127.0.0.1:{os.environ['E2E_PORT']}",
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["api.read", "api.write"],
            default_scopes=["api.read"],
        ),
    )
elif os.environ.get("E2E_AUTH"):
    from fastmcp.server.auth import RemoteAuthProvider
    from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
    from pydantic import AnyHttpUrl

    # Unauthenticated requests get a 401 pointing at the protected resource metadata.
    auth = RemoteAuthProvider(
        token_verifier=StaticTokenVerifier(
            tokens={
                "e2e-secret-token": {"client_id": "e2e", "scopes": ["api.read"]},
                "e2e-writer-token": {
                    "client_id": "e2e",
                    "scopes": ["api.read", "api.write"],
                },
            },
            required_scopes=[
                scope
                for scope in os.environ.get("E2E_REQUIRED_SCOPES", "").split(",")
                if scope
            ]
            or None,
        ),
        authorization_servers=[AnyHttpUrl("https://auth.example.test")],
        base_url=f"http://127.0.0.1:{os.environ['E2E_PORT']}",
        scopes_supported=["api.read", "api.write"],
    )

mcp = FastMCP("e2e-server", version=os.environ.get("E2E_VERSION", "9.9.9"), auth=auth)


def register(tool_name: str) -> None:
    @mcp.tool(name=tool_name)
    def tool() -> str:
        return f"{tool_name} called"


for name in os.environ.get("E2E_TOOLS", "get_order,list_orders").split(","):
    register(name.strip())

if os.environ.get("E2E_TRANSPORT") == "http":
    mcp.run(
        transport="http",
        host="127.0.0.1",
        port=int(os.environ["E2E_PORT"]),
        show_banner=False,
    )
else:
    mcp.run(show_banner=False)
