"""Loading the packs that a config asks for."""

from __future__ import annotations

import difflib
from collections.abc import Iterable

from mcph.config import Config, ConfigError
from mcph.extensions.pack import PackError
from mcph.extensions.registry import Registry, load_registry

ADD_A_PACK = (
    "add a pack to `extends` in mcph.yaml "
    "(`extends: [builtin:core]` has the rules that ship with mcph)"
)


def load_packs(config: Config) -> Registry:
    """Load the packs named by ``extends`` in the config.

    Relative directories are taken from the directory of the config file. Raises
    :class:`~mcph.config.ConfigError` if a pack cannot be loaded.
    """
    try:
        return load_registry(config.extends, config.base_dir)
    except PackError as exc:
        raise ConfigError(str(exc)) from exc


def load_extensions(config: Config) -> Registry:
    """Load the packs of the config, and check the ``doctor.rules`` settings against them.

    Raises :class:`~mcph.config.ConfigError` if a pack cannot be loaded or a setting
    names a rule that no loaded pack defines.
    """
    registry = load_packs(config)
    known = [rule.id for rule in registry.rules]
    unknown = [rule_id for rule_id in config.doctor.rules if rule_id not in known]
    if unknown:
        where = f" {config.path}" if config.path is not None else ""
        raise ConfigError(
            f"Invalid config{where}:\n  doctor.rules: {_unknown_rules_message(unknown, known)}"
        )
    return registry


def _unknown_rules_message(unknown: Iterable[str], known: list[str]) -> str:
    named = []
    for rule_id in unknown:
        text = repr(rule_id)
        closest = difflib.get_close_matches(rule_id, known, n=1)
        if closest:
            text += f" (did you mean {closest[0]!r}?)"
        named.append(text)
    noun = "rule" if len(named) == 1 else "rules"
    listed = ", ".join(named)
    if not known:
        return f"unknown {noun} {listed}: no rule is loaded; {ADD_A_PACK}"
    return f"unknown {noun} {listed}; known rules: {', '.join(known)}"
