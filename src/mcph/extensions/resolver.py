"""Finding the directory of a pack from the source named in ``mcph.yaml``.

A resolver turns a source string into a directory. The sources understood today
are a local directory (``./packs/acme``, relative to the directory of
``mcph.yaml``, or an absolute path) and ``builtin:<name>`` for a pack bundled with
mcph. Other kinds of source (a Git repository, for example) are more resolvers.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mcph.extensions.pack import MANIFEST_NAME, PackError

BUILTIN_PACKS_DIR = Path(__file__).parent / "builtin_packs"
BUILTIN_PREFIX = "builtin:"

# "builtin:x" and "git+https://..." have a scheme; "./packs", "/opt/p" and "packs/x" do not.
_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]+:")


@dataclass(frozen=True)
class ResolvedPack:
    """A source and the directory it stands for."""

    source: str
    directory: Path


class Resolver(Protocol):
    """Turns a source into a pack directory, if it is the resolver for that source."""

    def resolve(self, source: str, base_dir: Path) -> ResolvedPack | None:
        """The pack for ``source``, or ``None`` if this resolver is not responsible for it.

        Raises :class:`PackError` if it is responsible but cannot find the pack.
        """


class DirectoryResolver:
    """A local directory: relative to ``base_dir`` (the directory of ``mcph.yaml``), or absolute."""

    def resolve(self, source: str, base_dir: Path) -> ResolvedPack | None:
        if _SCHEME_RE.match(source):
            return None
        path = Path(source).expanduser()
        if not path.is_absolute():
            path = base_dir / path
        path = Path(os.path.abspath(path))
        if not path.exists():
            raise PackError(f"pack directory not found: {path} (from {source!r})")
        if not path.is_dir():
            raise PackError(f"pack source {source!r} is not a directory: {path}")
        return ResolvedPack(source, path)


class BuiltinResolver:
    """``builtin:<name>``: a pack bundled with mcph."""

    def __init__(self, root: Path = BUILTIN_PACKS_DIR) -> None:
        self._root = root

    def resolve(self, source: str, base_dir: Path) -> ResolvedPack | None:
        if not source.startswith(BUILTIN_PREFIX):
            return None
        name = source.removeprefix(BUILTIN_PREFIX)
        directory = self._root / name
        if not name or "/" in name or not (directory / MANIFEST_NAME).is_file():
            available = ", ".join(self.available()) or "none"
            raise PackError(
                f"no built-in pack named {name!r} in {source!r} (available: {available})"
            )
        return ResolvedPack(source, directory)

    def available(self) -> list[str]:
        """The names of the bundled packs."""
        if not self._root.is_dir():
            return []
        return sorted(
            path.name
            for path in self._root.iterdir()
            if (path / MANIFEST_NAME).is_file()
        )


def default_resolvers() -> tuple[Resolver, ...]:
    return (BuiltinResolver(), DirectoryResolver())


def resolve_pack(
    source: str, base_dir: Path, resolvers: Sequence[Resolver] | None = None
) -> ResolvedPack:
    """The directory of the pack that ``source`` names.

    The first resolver that is responsible for the source decides. Raises
    :class:`PackError` if there is none, or if it cannot find the pack.
    """
    if not source.strip():
        raise PackError("a pack source must not be empty")
    for resolver in resolvers if resolvers is not None else default_resolvers():
        resolved = resolver.resolve(source, base_dir)
        if resolved is not None:
            return resolved
    raise PackError(
        f"unsupported pack source {source!r}: use a directory or 'builtin:<name>'"
    )
