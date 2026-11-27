"""The ``mcph doctor`` pre-flight checks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from mcph.auth_probe import AuthInfo
from mcph.config import Config
from mcph.extensions import ADD_A_PACK, LoadedPack
from mcph.mcp_client import (
    DEFAULT_TIMEOUT_SECONDS,
    ServerError,
    ServerSnapshot,
    snapshot_server,
)
from mcph.rules import (
    Finding,
    Outcome,
    Rule,
    RuleContext,
    RuleInfo,
    RuleResult,
    Severity,
    apply_rule_settings,
    is_off,
    run_rule,
)

NO_RULES = (
    "no rules are loaded, so the servers were only reached, not checked: "
    f"{ADD_A_PACK}."
)


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
    warnings: tuple[str, ...] = ()
    """Warnings about the run as a whole, such as that no rule was loaded."""

    @property
    def ok(self) -> bool:
        return all(server.ok for server in self.servers)

    @property
    def warning_count(self) -> int:
        return len(self.warnings) + sum(server.warning_count for server in self.servers)

    def passed(self, *, strict: bool = False) -> bool:
        """Whether the run succeeded; with ``strict``, warnings count as failures."""
        return self.ok and not (strict and self.warning_count)


async def run_doctor(
    config: Config,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    rules: Iterable[Rule] = (),
) -> DoctorReport:
    """Check every server in ``config``, one after another.

    Each server is checked against the ``rules``, which come from the packs the
    config extends (see :func:`mcph.extensions.load_extensions`); without any rule
    the servers are only reached, and the report warns about it. A rule is
    skipped for a server it is not about, or if the server could not be reached.
    A failing server does not stop the others from being checked. The
    ``doctor.rules`` settings of the config decide which rules run and how
    serious their findings are, and the report says what happened to every rule.
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
        context = RuleContext(server, timeout, snapshot=snapshot, error=error)
        results = [
            await _result(rule, context, settings)
            for rule in rules
            if rule.applies_to(server)
        ]
        reports.append(
            ServerReport(
                server.name, snapshot=snapshot, error=error, rules=tuple(results)
            )
        )
    return DoctorReport(tuple(reports), warnings=() if rules else (NO_RULES,))


async def _result(
    rule: Rule, context: RuleContext, settings: Mapping[str, str]
) -> RuleResult:
    if is_off(rule.id, settings):  # it does not run
        return RuleResult.off(rule.id)
    return apply_rule_settings(await run_rule(rule, context), settings)


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

    for warning in report.warnings:
        lines.extend(["", f"Warning: {warning}"])

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
    packs: Iterable[LoadedPack],
    settings: Mapping[str, str],
    *,
    config_path: Path | None,
) -> str:
    """Render the rules of the packs, with the setting that applies to each.

    ``settings`` is the ``doctor.rules`` section of the config; a rule that is not
    in it has its default severity. ``config_path`` is the file the settings come
    from, or ``None`` if there is none.
    """
    packs = list(packs)
    if config_path is not None:
        source = f"settings from {config_path}"
    else:
        source = "no mcph.yaml in the current directory"
    rows = [
        (
            pack,
            [
                (rule.id, _setting_text(rule, settings), rule.description)
                for rule in pack.rules
            ],
        )
        for pack in packs
    ]
    every_row = [row for _, pack_rows in rows for row in pack_rows]
    id_width = max((len(rule_id) for rule_id, _, _ in every_row), default=0)
    setting_width = max((len(setting) for _, setting, _ in every_row), default=0)

    lines = [f"Rules ({source})"]
    for pack, pack_rows in rows:
        lines.extend(["", f"{pack.manifest.name} ({pack.source})"])
        if not pack_rows:
            lines.append("  (no rules)")
        lines.extend(
            f"  {rule_id:<{id_width}}  {setting:<{setting_width}}  {description}".rstrip()
            for rule_id, setting, description in pack_rows
        )
    if not every_row:
        lines.extend(["", f"No rules are loaded: {ADD_A_PACK}."])
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
