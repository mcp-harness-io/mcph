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

mcp = FastMCP("e2e-server", version=os.environ.get("E2E_VERSION", "9.9.9"))


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
