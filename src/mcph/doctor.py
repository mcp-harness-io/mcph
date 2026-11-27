"""The ``mcph doctor`` pre-flight checks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mcph.config import Config
from mcph.mcp_client import (
    DEFAULT_TIMEOUT_SECONDS,
    ServerError,
    ServerSnapshot,
    snapshot_stdio_server,
)


@dataclass(frozen=True)
class ServerReport:
    """The outcome of checking one configured server."""

    name: str
    snapshot: ServerSnapshot | None = None
    error: ServerError | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class DoctorReport:
    """The outcome of checking every configured server."""

    servers: tuple[ServerReport, ...]

    @property
    def ok(self) -> bool:
        return all(server.ok for server in self.servers)


async def run_doctor(
    config: Config, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> DoctorReport:
    """Check every server in ``config``, one after another.

    A failing server does not stop the others from being checked.
    """
    reports: list[ServerReport] = []
    for server in config.servers:
        try:
            snapshot = await snapshot_stdio_server(
                server, cwd=config.base_dir, timeout=timeout
            )
        except ServerError as exc:
            reports.append(ServerReport(server.name, error=exc))
        else:
            reports.append(ServerReport(server.name, snapshot=snapshot))
    return DoctorReport(tuple(reports))


def format_report(report: DoctorReport, *, config_path: Path | None = None) -> str:
    """Render a report as plain text."""
    count = len(report.servers)
    header = f"Checking {_plural(count, 'server')}"
    if config_path is not None:
        header += f" from {config_path}"
    lines = [header, ""]

    for server in report.servers:
        lines.append(server.name)
        if server.snapshot is not None:
            lines.append(_status("OK", _describe(server.snapshot)))
        elif server.error is not None:
            lines.append(_status("ERROR", server.error.message))
            if server.error.stderr_tail:
                lines.append(_continuation("server stderr:"))
                lines.extend(
                    _continuation("  " + line)
                    for line in server.error.stderr_tail.splitlines()
                )

    failed = sum(1 for server in report.servers if not server.ok)
    lines.append("")
    lines.append(
        f"{failed} of {_plural(count, 'server')} failed." if failed else "All servers OK."
    )
    return "\n".join(lines)


def _describe(snapshot: ServerSnapshot) -> str:
    identity = " ".join(
        part for part in (snapshot.server_name, snapshot.server_version) if part
    )
    details = [identity] if identity else []
    if snapshot.protocol_version:
        details.append(f"MCP {snapshot.protocol_version}")

    text = f"started in {round(snapshot.startup_seconds * 1000)} ms"
    if details:
        text += f" ({', '.join(details)})"
    return f"{text}, {_plural(len(snapshot.tools), 'tool')}"


def _status(label: str, text: str) -> str:
    return f"  {label:<5}  {text}"


def _continuation(text: str) -> str:
    return " " * 9 + text


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"
