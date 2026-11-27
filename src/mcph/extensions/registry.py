"""Loading packs and collecting what they contribute.

A pack file marks its rule functions with :func:`doctor_rule`::

    from mcph.extensions import doctor_rule

    @doctor_rule(id="acme.no_delete_tools", description="No tool deletes data.")
    def no_delete_tools(context):
        return [f"{t.name} deletes data" for t in context.snapshot.tools if "delete" in t.name]

The loader imports the files that the manifest lists and collects the marked
functions into a :class:`Registry`. A rule file is imported on its own, so it can
import mcph and installed packages but not its sibling files.

The Python in a pack runs with the user's privileges, like the ``command`` of a
``stdio`` server in ``mcph.yaml``. That is acceptable for the local directories and
the bundled packs mcph loads today; remote sources will need a trust decision.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from mcph.extensions.pack import MANIFEST_NAME, PackError, PackManifest, load_manifest
from mcph.extensions.resolver import ResolvedPack, Resolver, resolve_pack
from mcph.rules import Rule, Severity

RULE_ATTRIBUTE = "__mcph_rule__"

# namespace.name: the namespace may contain '-' (such as `claude-code.max_depth`).
_RULE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]*\.[a-z0-9][a-z0-9_]*")
_TRANSPORTS = ("stdio", "http")


def doctor_rule(
    *,
    id: str,
    description: str,
    severity: Severity | str = Severity.WARN,
    transports: Sequence[str] = _TRANSPORTS,
    needs_connection: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark a function as a doctor rule of its pack.

    The function takes a :class:`~mcph.rules.RuleContext` and returns the problems
    it finds (see :class:`~mcph.rules.Rule`). The function itself is returned
    unchanged, so it can still be called in tests.
    """
    if not _RULE_ID_RE.fullmatch(id):
        raise ValueError(
            f"rule id {id!r} must look like 'namespace.name': lowercase letters, "
            "digits and '_' (and '-' in the namespace)"
        )
    if not description.strip():
        raise ValueError(f"rule {id!r} needs a description")
    try:
        default = Severity(severity)
    except ValueError:
        raise ValueError(
            f"rule {id!r}: severity must be 'warn' or 'error', not {severity!r}"
        ) from None
    unknown = [kind for kind in transports if kind not in _TRANSPORTS]
    if not transports or unknown:
        raise ValueError(
            f"rule {id!r}: transports must be a non-empty subset of {list(_TRANSPORTS)}"
        )

    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        if hasattr(function, RULE_ATTRIBUTE):
            raise ValueError(f"{function.__name__} is already a rule")
        rule = Rule(
            id,
            description,
            default,
            function,
            transports=tuple(transports),
            needs_connection=needs_connection,
        )
        setattr(function, RULE_ATTRIBUTE, rule)
        return function

    return decorate


@dataclass(frozen=True)
class LoadedPack:
    """A pack whose manifest was read and whose rule files were imported."""

    manifest: PackManifest
    source: str
    """The source as written in ``mcph.yaml``."""
    directory: Path
    rules: tuple[Rule, ...]
    """The rules of the pack in the order of its manifest and of their files."""


def load_pack(resolved: ResolvedPack) -> LoadedPack:
    """Read the manifest of a resolved pack and import the files that define its rules.

    Raises :class:`PackError` if the manifest is invalid, a listed file is missing,
    fails to import or defines no rule, or two rules have the same id.
    """
    manifest = load_manifest(resolved.directory)
    name = manifest.name
    defined: dict[str, tuple[Rule, str]] = {}  # rule id -> (rule, file)
    for file in manifest.rules:
        path = resolved.directory / file
        if not path.is_file():
            raise PackError(
                f"pack {name!r}: {file}, listed under rules in {MANIFEST_NAME}, does not exist"
            )
        found = _rules_of(_import(name, path))
        if not found:
            raise PackError(
                f"pack {name!r}: {file} defines no rule "
                "(mark each rule function with @doctor_rule)"
            )
        for rule in found:
            earlier = defined.get(rule.id)
            if earlier is None:
                defined[rule.id] = (rule, file)
            elif earlier[0] is not rule:  # the same function reached twice is fine
                raise PackError(
                    f"pack {name!r}: the rule id {rule.id!r} is defined in "
                    f"{earlier[1]} and in {file}"
                )
    rules = tuple(rule for rule, _ in defined.values())
    return LoadedPack(manifest, resolved.source, resolved.directory, rules)


def _import(pack: str, path: Path) -> ModuleType:
    digest = hashlib.sha256(str(path).encode()).hexdigest()[:8]
    label = re.sub(r"[^A-Za-z0-9_]", "_", pack)
    module_name = f"mcph_pack_{label}_{digest}_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None  # the manifest only lists .py files
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # dataclasses and typing look modules up here
    try:
        spec.loader.exec_module(module)
    except (Exception, SystemExit) as exc:
        sys.modules.pop(module_name, None)
        raise PackError(
            f"pack {pack!r}: cannot import {path.name}: {type(exc).__name__}: {exc}"
        ) from exc
    return module


def _rules_of(module: ModuleType) -> list[Rule]:
    rules = []
    for value in vars(module).values():
        rule = getattr(value, RULE_ATTRIBUTE, None)
        if isinstance(rule, Rule) and rule.check is value:
            rules.append(rule)
    return rules


class Registry:
    """What the loaded packs contribute, in the order the packs were loaded."""

    def __init__(self) -> None:
        self._packs: list[LoadedPack] = []
        self._owners: dict[str, LoadedPack] = {}

    def add(self, pack: LoadedPack) -> None:
        """Add a pack. Raises :class:`PackError` if its name or one of its rule ids is taken."""
        name = pack.manifest.name
        for loaded in self._packs:
            if loaded.manifest.name == name:
                raise PackError(
                    f"two packs are called {name!r}: {loaded.source!r} and {pack.source!r}"
                )
        for rule in pack.rules:
            owner = self._owners.get(rule.id)
            if owner is not None:
                raise PackError(
                    f"the rule {rule.id!r} is defined by two packs: "
                    f"{owner.manifest.name!r} ({owner.source!r}) and {name!r} ({pack.source!r})"
                )
        self._packs.append(pack)
        for rule in pack.rules:
            self._owners[rule.id] = pack

    @property
    def packs(self) -> tuple[LoadedPack, ...]:
        return tuple(self._packs)

    @property
    def rules(self) -> tuple[Rule, ...]:
        return tuple(rule for pack in self._packs for rule in pack.rules)

    def pack_of(self, rule_id: str) -> LoadedPack:
        """The pack that defines the rule. Raises :class:`KeyError` for an unknown id."""
        return self._owners[rule_id]


def load_registry(
    sources: Sequence[str],
    base_dir: Path,
    resolvers: Sequence[Resolver] | None = None,
) -> Registry:
    """Resolve and load the packs named by ``sources``, in that order.

    Relative directories are taken from ``base_dir`` (the directory of
    ``mcph.yaml``). Raises :class:`PackError` for the first problem.
    """
    registry = Registry()
    for source in sources:
        registry.add(load_pack(resolve_pack(source, base_dir, resolvers)))
    return registry
