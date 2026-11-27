"""The ``mcph doctor`` pre-flight checks."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from mcph.auth_probe import AuthInfo
from mcph.config import Config, HttpServerConfig, OAuthAuthConfig, ServerConfig
from mcph.mcp_client import (
    DEFAULT_TIMEOUT_SECONDS,
    ServerError,
    ServerSnapshot,
    snapshot_server,
)
from mcph.oauth_audit import audit_authorization_server
from mcph.rules import BUILTIN_RULES, Finding, Rule, Severity, run_rules


@dataclass(frozen=True)
class ServerReport:
    """The outcome of checking one configured server."""

    name: str
    snapshot: ServerSnapshot | None = None
    error: ServerError | None = None
    findings: tuple[Finding, ...] = ()

    @property
    def ok(self) -> bool:
        """No failure to start or query the server, and no error-level finding."""
        return self.error is None and not any(
            finding.severity is Severity.ERROR for finding in self.findings
        )

    @property
    def warning_count(self) -> int:
        return sum(1 for finding in self.findings if finding.severity is Severity.WARN)


@dataclass(frozen=True)
class DoctorReport:
    """The outcome of checking every configured server."""

    servers: tuple[ServerReport, ...]

    @property
    def ok(self) -> bool:
        return all(server.ok for server in self.servers)

    @property
    def warning_count(self) -> int:
        return sum(server.warning_count for server in self.servers)

    def passed(self, *, strict: bool = False) -> bool:
        """Whether the run succeeded; with ``strict``, warnings count as failures."""
        return self.ok and not (strict and self.warning_count)


async def run_doctor(
    config: Config,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    rules: Iterable[Rule] = BUILTIN_RULES,
) -> DoctorReport:
    """Check every server in ``config``, one after another.

    Each server that starts is also checked against ``rules``. A failing server
    does not stop the others from being checked.
    """
    rules = tuple(rules)
    reports: list[ServerReport] = []
    for server in config.servers:
        try:
            snapshot = await snapshot_server(
                server, cwd=config.base_dir, timeout=timeout
            )
        except ServerError as exc:
            findings = await _oauth_findings(server, exc, timeout)
            reports.append(ServerReport(server.name, error=exc, findings=findings))
        else:
            findings = tuple(run_rules(snapshot, rules))
            reports.append(
                ServerReport(server.name, snapshot=snapshot, findings=findings)
            )
    return DoctorReport(tuple(reports))


async def _oauth_findings(
    server: ServerConfig, error: ServerError, timeout: float
) -> tuple[Finding, ...]:
    """Problems with the OAuth setup of a server that needs authentication."""
    if error.auth is None or not isinstance(server, HttpServerConfig):
        return ()
    accepted = (
        isinstance(server.auth, OAuthAuthConfig)
        and server.auth.ignore_issuer_trailing_slash
    )
    return await audit_authorization_server(
        error.auth, server.url, timeout=timeout, issuer_slash_accepted=accepted
    )


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
            lines.extend(_finding_line(finding) for finding in server.findings)
        elif server.error is not None:
            lines.append(_status("ERROR", server.error.message))
            if server.error.auth is not None:
                lines.extend(
                    _continuation(line) for line in _auth_lines(server.error.auth)
                )
            if server.error.stderr_tail:
                lines.append(_continuation("server stderr:"))
                lines.extend(
                    _continuation("  " + line)
                    for line in server.error.stderr_tail.splitlines()
                )
            lines.extend(_finding_line(finding) for finding in server.findings)

    failed = sum(1 for server in report.servers if not server.ok)
    summary = (
        f"{failed} of {_plural(count, 'server')} failed" if failed else "All servers OK"
    )
    if report.warning_count:
        summary += f", {_plural(report.warning_count, 'warning')}"
    lines.append("")
    lines.append(summary + ".")
    return "\n".join(lines)


def _describe(snapshot: ServerSnapshot) -> str:
    identity = " ".join(
        part for part in (snapshot.server_name, snapshot.server_version) if part
    )
    details = [identity] if identity else []
    if snapshot.protocol_version:
        details.append(f"MCP {snapshot.protocol_version}")

    verb = "connected" if snapshot.transport == "http" else "started"
    text = f"{verb} in {round(snapshot.startup_seconds * 1000)} ms"
    if details:
        text += f" ({', '.join(details)})"
    return f"{text}, {_plural(len(snapshot.tools), 'tool')}"


def _auth_lines(auth: AuthInfo) -> list[str]:
    """Describe what an HTTP server that refused us says about its auth."""
    lines: list[str] = []
    if auth.error:
        lines.append(f"error: {auth.error}")
    if auth.error_description:
        lines.append(f"server says: {auth.error_description}")
    if auth.required_scope:
        lines.append(f"scope the server asks for: {auth.required_scope}")

    parsed = bool(lines)
    if not parsed:  # nothing was picked out of the challenge: show it as sent
        if auth.challenge:
            lines.append(f"challenge: {auth.challenge}")
        else:
            lines.append(
                f"challenge: none (the {auth.status} had no WWW-Authenticate header)"
            )

    if auth.metadata_url is None:
        return [*lines, "no OAuth protected resource metadata found"]
    if parsed or not auth.challenge or auth.metadata_url not in auth.challenge:
        lines.append(f"metadata: {auth.metadata_url}")
    servers = auth.authorization_servers
    if servers:
        label = "authorization servers" if len(servers) > 1 else "authorization server"
        lines.append(f"{label}: {', '.join(servers)}")
    if auth.scopes:
        lines.append(f"scopes: {', '.join(auth.scopes)}")
    return lines


def _finding_line(finding: Finding) -> str:
    label = finding.severity.value.upper()
    return _status(label, f"{finding.rule_id}: {finding.message}")


def _status(label: str, text: str) -> str:
    return f"  {label:<5}  {text}"


def _continuation(text: str) -> str:
    return " " * 9 + text


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"
