import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx2
import pytest
import yaml
from typer.testing import CliRunner

from mcph.cli import app

DEMO_SERVER = Path(__file__).with_name("demo_server.py")


class FakeServerUrl(str):
    """The URL of a fake server's MCP endpoint, which also knows what it was asked for."""

    hits: list[str]
    """The paths that were requested from the server so far, in order."""


class Project:
    """A temporary project directory holding a copy of the demo server.

    The server is referenced by the relative path ``server.py``, so these tests
    only pass if servers really run from the config file's directory.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        shutil.copy(DEMO_SERVER, root / "server.py")

    @property
    def config_path(self) -> Path:
        return self.root / "mcph.yaml"

    def demo_server(self, name: str = "demo", **options: object) -> dict:
        """A ``servers`` entry for the demo server.

        Keyword options become ``E2E_*`` environment variables of the server,
        e.g. ``tools="a,b"`` sets ``E2E_TOOLS=a,b``.
        """
        return {
            "name": name,
            "transport": "stdio",
            "command": sys.executable,
            "args": ["server.py"],
            "env": {f"E2E_{key.upper()}": str(value) for key, value in options.items()},
        }

    @staticmethod
    def http_entry(
        name: str,
        url: str,
        token_env: str | None = None,
        oauth: bool | dict = False,
    ) -> dict:
        """A ``servers`` entry for an already running HTTP server.

        With ``token_env`` the entry sends the bearer token from that variable;
        with ``oauth`` it uses the token stored by ``mcph auth login`` (pass a
        dict for extra ``auth`` settings).
        """
        entry = {"name": name, "transport": "http", "url": str(url)}  # YAML wants a plain str
        if token_env:
            entry["auth"] = {"type": "bearer", "token_env": token_env}
        elif oauth:
            entry["auth"] = {"type": "oauth", **(oauth if isinstance(oauth, dict) else {})}
        return entry

    def write_config(
        self,
        *servers: dict,
        rules: dict[str, str] | None = None,
        extends: Sequence[str] | None = ("builtin:core",),
    ) -> Path:
        """Write ``mcph.yaml``.

        ``rules`` becomes its ``doctor.rules`` section. ``extends`` lists the packs
        whose rules are used (by default the rules that ship with mcph).
        """
        config: dict = {"servers": list(servers)}
        if extends:
            config["extends"] = list(extends)
        if rules is not None:
            config["doctor"] = {"rules": rules}
        self.config_path.write_text(yaml.safe_dump(config))
        return self.config_path

    def mcph(self, *args: str):
        """Run ``mcph -c <config> <args>`` in-process; servers are real subprocesses."""
        return CliRunner().invoke(app, ["-c", str(self.config_path), *args])

    def doctor(self, *args: str):
        return self.mcph("doctor", "run", *args)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    root = tmp_path / "project"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    # Never run from the project directory, to prove paths do not depend on cwd.
    monkeypatch.chdir(elsewhere)
    return Project(root)


@pytest.fixture
def wait_until_gone():
    """Return a function that waits for a process to disappear.

    The function returns True if the process is gone within ``timeout`` seconds.
    """

    def wait(pid: int, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.05)
        return False

    return wait


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def start_http_server(project: Project, tmp_path: Path):
    """Return a function that starts the demo server over HTTP and gives its URL.

    Keyword options are passed like for ``Project.demo_server``. Every server
    started this way is stopped when the test ends.
    """
    processes: list[tuple[subprocess.Popen, object]] = []

    def start(**options: object) -> str:
        port = free_port()
        env = {**os.environ, "E2E_TRANSPORT": "http", "E2E_PORT": str(port)}
        env.update({f"E2E_{key.upper()}": str(value) for key, value in options.items()})
        log_path = tmp_path / f"http-server-{port}.log"
        log = log_path.open("w")
        process = subprocess.Popen(
            [sys.executable, "server.py"],
            cwd=project.root,
            env=env,
            stdout=log,
            stderr=log,
        )
        processes.append((process, log))

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(f"demo server exited early:\n{log_path.read_text()}")
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                return f"http://127.0.0.1:{port}/mcp"
            except OSError:
                time.sleep(0.05)
        pytest.fail(f"demo server did not listen in time:\n{log_path.read_text()}")

    yield start

    for process, log in processes:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()


@pytest.fixture
def closed_port_url() -> str:
    """URL of a port that nothing is listening on."""
    return f"http://127.0.0.1:{free_port()}/mcp"


@pytest.fixture
def silent_server_url():
    """URL of a TCP server that accepts connections but never answers."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        yield f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"


@pytest.fixture
def plain_401_server_url():
    """URL of a server that answers every request with 401 and no OAuth metadata."""

    class Handler(BaseHTTPRequestHandler):
        def respond(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer realm="demo"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = do_POST = respond

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/mcp"
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
def browser(monkeypatch: pytest.MonkeyPatch):
    """Replace the web browser by a user who approves every sign-in at once.

    Yields the list of URLs "opened" so far.
    """
    opened: list[str] = []
    threads: list[threading.Thread] = []

    def open_url(url: str, *args: object, **kwargs: object) -> bool:
        opened.append(url)

        def approve() -> None:
            # The loopback server that receives the redirect starts a moment later.
            for _ in range(100):
                try:
                    httpx2.get(url, follow_redirects=True, timeout=5)
                    return
                except httpx2.HTTPError:
                    time.sleep(0.1)

        thread = threading.Thread(target=approve, daemon=True)
        thread.start()
        threads.append(thread)
        return True

    monkeypatch.setattr(webbrowser, "open", open_url)
    yield opened
    for thread in threads:
        thread.join(timeout=10)


@pytest.fixture
def idle_browser(monkeypatch: pytest.MonkeyPatch):
    """A web browser that opens but where nobody ever signs in."""
    opened: list[str] = []
    monkeypatch.setattr(
        webbrowser, "open", lambda url, *args, **kwargs: opened.append(url) or True
    )
    return opened


@pytest.fixture
def fake_oauth_server():
    """Start a server whose OAuth documents can be set to anything.

    Returns ``start(announced=None, missing=False, **metadata)``, which gives the
    URL of its MCP endpoint (a ``FakeServerUrl``: its ``hits`` list the paths the
    server was asked for). The endpoint answers 401 and points at protected
    resource metadata that announces ``announced`` (default: the server itself,
    without a trailing slash) as the authorization server. Keyword arguments
    override the authorization server's metadata (``None`` removes a key);
    ``missing=True`` makes that metadata a 404. In string values ``{base}`` stands
    for the server's own URL, so ``announced="{base}/"`` announces it with a slash.
    """
    servers: list[HTTPServer] = []
    threads: list[threading.Thread] = []

    def start(
        announced: str | None = None, missing: bool = False, **metadata: object
    ) -> FakeServerUrl:
        class Handler(BaseHTTPRequestHandler):
            def respond(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                state = self.server.state
                path = self.path.split("?")[0]
                state["hits"].append(path)
                if path == "/mcp":
                    status, headers, body = 401, {
                        "WWW-Authenticate": "Bearer resource_metadata="
                        f'"{state["base"]}/.well-known/oauth-protected-resource/mcp"'
                    }, None
                elif path == "/.well-known/oauth-protected-resource/mcp":
                    status, headers, body = 200, {}, {
                        "resource": f"{state['base']}/mcp",
                        "authorization_servers": [state["announced"]],
                        "scopes_supported": ["api.read"],
                    }
                elif path == "/.well-known/oauth-authorization-server" and not state["missing"]:
                    status, headers, body = 200, {}, state["metadata"]
                else:
                    status, headers, body = 404, {}, None
                payload = b"" if body is None else json.dumps(body).encode()
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                if body is not None:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = respond

            def log_message(self, *args: object) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        base = f"http://127.0.0.1:{server.server_port}"
        defaults = {
            "issuer": base,
            "authorization_endpoint": f"{base}/authorize",
            "token_endpoint": f"{base}/token",
            "registration_endpoint": f"{base}/register",
            "code_challenge_methods_supported": ["S256"],
        }
        def fill(value: object) -> object:
            return value.replace("{base}", base) if isinstance(value, str) else value

        merged = {**defaults, **{k: fill(v) for k, v in metadata.items()}}
        server.state = {
            "base": base,
            "announced": fill(announced) if announced else base,
            "missing": missing,
            "metadata": {k: v for k, v in merged.items() if v is not None},
            "hits": [],
        }
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        threads.append(thread)
        url = FakeServerUrl(f"{base}/mcp")
        url.hits = server.state["hits"]
        return url

    yield start

    for server, thread in zip(servers, threads):
        server.shutdown()
        server.server_close()
        thread.join()

