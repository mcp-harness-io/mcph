"""The ``mcph doctor`` pre-flight checks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
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
from mcph.rules import (
    BUILTIN_RULES,
    OAUTH_RULES,
    Finding,
    Outcome,
    Rule,
    RuleInfo,
    RuleResult,
    Severity,
    apply_rule_settings,
    is_off,
    run_rule,
)

# Why rules were skipped when the rule itself has no say in it.
NO_CONNECTION = "mcph could not connect to the server"
NOT_NEEDED = "not needed, the server accepted the connection"


@dataclass(frozen=True)
class ServerReport:
    """The outcome of checking one configured server."""

    name: str
    snapshot: ServerSnapshot | None = None
    error: ServerError | None = None
    rules: tuple[RuleResult, ...] = ()
    """What happened to each rule on this server, in the order they are shown."""

    @property
    def findings(self) -> tuple[Finding, ...]:
        """The problems the rules found, in order."""
        return tuple(finding for result in self.rules for finding in result.findings)

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
    does not stop the others from being checked. The ``doctor.rules`` settings of
    the config decide which rules run and how serious their findings are, and the
    report says what happened to every rule.
    """
    settings = config.doctor.rules
    rules = tuple(rules)
    reports: list[ServerReport] = []
    for server in config.servers:
        snapshot = error = None
        try:
            snapshot = await snapshot_server(
                server, cwd=config.base_dir, timeout=timeout
            )
        except ServerError as exc:
            error = exc
        results = [
            *_snapshot_results(snapshot, rules, settings),
            *await _oauth_results(server, error, timeout, settings),
        ]
        reports.append(
            ServerReport(
                server.name, snapshot=snapshot, error=error, rules=tuple(results)
            )
        )
    return DoctorReport(tuple(reports))


def _snapshot_results(
    snapshot: ServerSnapshot | None, rules: Iterable[Rule], settings: Mapping[str, str]
) -> list[RuleResult]:
    """The rules that look at what a server exposes, run on its snapshot."""
    results = []
    for rule in rules:
        if is_off(rule.id, settings):  # it does not run
            results.append(RuleResult.off(rule.id))
        elif snapshot is None:
            results.append(RuleResult.skipped(rule.id, NO_CONNECTION))
        else:
            results.append(apply_rule_settings(run_rule(rule, snapshot), settings))
    return results


async def _oauth_results(
    server: ServerConfig,
    error: ServerError | None,
    timeout: float,
    settings: Mapping[str, str],
) -> list[RuleResult]:
    """The OAuth checks, which are about HTTP servers only."""
    if not isinstance(server, HttpServerConfig):
        return []
    if all(is_off(rule.id, settings) for rule in OAUTH_RULES):
        return [RuleResult.off(rule.id) for rule in OAUTH_RULES]  # nothing to ask
    return [
        apply_rule_settings(result, settings)
        for result in await _audit(server, error, timeout)
    ]


async def _audit(
    server: HttpServerConfig, error: ServerError | None, timeout: float
) -> tuple[RuleResult, ...]:
    """What the OAuth checks make of a server, as if none of them were turned off."""
    if error is None:
        return _skipped_oauth_rules(NOT_NEEDED)
    if error.auth is None:
        return _skipped_oauth_rules(NO_CONNECTION)
    accepted = (
        isinstance(server.auth, OAuthAuthConfig)
        and server.auth.ignore_issuer_trailing_slash
    )
    return await audit_authorization_server(
        error.auth, server.url, timeout=timeout, issuer_slash_accepted=accepted
    )


def _skipped_oauth_rules(reason: str) -> tuple[RuleResult, ...]:
    return tuple(RuleResult.skipped(rule.id, reason) for rule in OAUTH_RULES)


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
        lines.extend(_rule_lines(server.rules))

    failed = sum(1 for server in report.servers if not server.ok)
    summary = (
        f"{failed} of {_plural(count, 'server')} failed" if failed else "All servers OK"
    )
    if report.warning_count:
        summary += f", {_plural(report.warning_count, 'warning')}"
    lines.append("")
    lines.append(summary + ".")
    return "\n".join(lines)


def format_rules(
    rules: Iterable[RuleInfo],
    settings: Mapping[str, str],
    *,
    config_path: Path | None,
) -> str:
    """Render the rules with the setting that applies to each.

    ``settings`` is the ``doctor.rules`` section of the config; a rule that is not
    in it has its default severity. ``config_path`` is the file the settings come
    from, or ``None`` if there is none.
    """
    if config_path is not None:
        source = f"settings from {config_path}"
    else:
        source = "default settings: no mcph.yaml in the current directory"
    rows = [
        (rule.id, _setting_text(rule, settings), rule.description) for rule in rules
    ]
    id_width = max((len(rule_id) for rule_id, _, _ in rows), default=0)
    setting_width = max((len(setting) for _, setting, _ in rows), default=0)
    lines = [f"Rules ({source})", ""]
    lines.extend(
        f"  {rule_id:<{id_width}}  {setting:<{setting_width}}  {description}".rstrip()
        for rule_id, setting, description in rows
    )
    return "\n".join(lines)


def _setting_text(rule: RuleInfo, settings: Mapping[str, str]) -> str:
    """``warn``, or ``error (default: warn)`` when the config changed the default."""
    default = rule.severity.value
    setting = settings.get(rule.id, default)
    return setting if setting == default else f"{setting} (default: {default})"


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


def _rule_lines(results: Iterable[RuleResult]) -> list[str]:
    """One line per rule that ran or is off; skipped rules share a line per reason."""
    results = list(results)
    skipped: dict[str, list[str]] = {}
    for result in results:
        if result.outcome is Outcome.SKIPPED:
            skipped.setdefault(result.reason, []).append(result.rule_id)

    lines: list[str] = []
    for result in results:
        if result.outcome is Outcome.OFF:
            lines.append(_status("OFF", result.rule_id))
        elif result.outcome is Outcome.SKIPPED:
            ids = skipped.pop(result.reason, None)  # the first of its kind lists them all
            if ids is not None:
                lines.append(_status("SKIP", f"{', '.join(ids)}: {result.reason}"))
        elif result.findings:
            lines.extend(_finding_line(finding) for finding in result.findings)
        else:
            lines.append(_status("OK", result.rule_id))
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
