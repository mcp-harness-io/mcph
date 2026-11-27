import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from mcph.cli import app

DEMO_SERVER = Path(__file__).with_name("demo_server.py")


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
    def http_entry(name: str, url: str) -> dict:
        """A ``servers`` entry for an already running HTTP server."""
        return {"name": name, "transport": "http", "url": url}

    def write_config(self, *servers: dict) -> Path:
        self.config_path.write_text(yaml.safe_dump({"servers": list(servers)}))
        return self.config_path

    def doctor(self, *args: str):
        """Run ``mcph -c <config> doctor`` in-process; the servers are real subprocesses."""
        return CliRunner().invoke(app, ["-c", str(self.config_path), "doctor", *args])


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

