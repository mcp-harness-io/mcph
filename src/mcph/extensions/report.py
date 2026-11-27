"""Rendering what ``mcph pack list`` and ``mcph pack verify`` find out."""

from __future__ import annotations

import textwrap
from collections.abc import Iterable
from pathlib import Path

from mcph.extensions.loading import ADD_A_PACK
from mcph.extensions.registry import LoadedPack, PackReport


def format_packs(packs: Iterable[LoadedPack], *, config_path: Path | None) -> str:
    """Render the loaded packs: name, version, source, description and rule ids.

    ``config_path`` is the config the packs were named in, or ``None`` if there is
    none.
    """
    packs = list(packs)
    if config_path is not None:
        source = f"from {config_path}"
    else:
        source = "no mcph.yaml in the current directory"
    lines = [f"Packs ({source})"]
    for pack in packs:
        manifest = pack.manifest
        lines.extend(["", f"{manifest.name} {manifest.version} ({pack.source})"])
        if manifest.description:
            lines.append(f"  {manifest.description}")
        lines.extend(f"  {rule.id}" for rule in pack.rules)
        if not pack.rules:
            lines.append("  (no rules)")
    if not packs:
        lines.extend(["", f"No packs are loaded: {ADD_A_PACK}."])
    return "\n".join(lines)


def format_verification(report: PackReport, *, path: str) -> str:
    """Render what checking the pack at ``path`` found: its problems, or its rules."""
    if report.problems:
        lines = [f"{path}: {_plural(len(report.problems), 'problem')}"]
        lines.extend(textwrap.indent(problem, "  ") for problem in report.problems)
        return "\n".join(lines)

    pack = report.pack
    assert pack is not None  # a pack without problems has a manifest
    manifest = pack.manifest
    lines = [f"{manifest.name} {manifest.version}: valid, {_plural(len(pack.rules), 'rule')}"]
    width = max((len(rule.id) for rule in pack.rules), default=0)
    lines.extend(
        f"  {rule.id:<{width}}  {rule.severity.value:<5}  {rule.description}"
        for rule in pack.rules
    )
    return "\n".join(lines)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"
