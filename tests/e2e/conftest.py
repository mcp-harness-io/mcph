import os
import shutil
import sys
import time
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
