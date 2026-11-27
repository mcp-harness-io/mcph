"""The manifest of a Harness Pack: ``mcph-pack.yaml``.

A pack is a directory with a manifest at its root. The manifest names the pack and
lists the Python files that define its rules.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MANIFEST_NAME = "mcph-pack.yaml"

_PACK_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")


class PackError(Exception):
    """A pack could not be found, read or loaded."""


class PackManifest(BaseModel):
    """The contents of ``mcph-pack.yaml``."""

    # Unknown keys are rejected, so what a pack declares that this mcph does not
    # support yet (runners, checks, ...) fails loudly instead of being ignored.
    model_config = ConfigDict(extra="forbid")

    name: str
    """What the pack is called: lowercase letters, digits, ``_`` and ``-``."""
    version: str = Field(min_length=1)
    """The version of the pack, as a string (``"1.0.0"``)."""
    description: str = ""
    rules: list[str] = Field(default_factory=list)
    """The Python files of the pack that define rules, relative to the pack."""

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not _PACK_NAME_RE.fullmatch(value):
            raise ValueError(
                "must start with a lowercase letter or digit and contain only "
                "lowercase letters, digits, '_' and '-'"
            )
        return value

    @field_validator("rules")
    @classmethod
    def _check_rules(cls, files: list[str]) -> list[str]:
        seen: set[str] = set()
        for file in files:
            path = PurePosixPath(file)
            if path.is_absolute() or ".." in path.parts or path.suffix != ".py":
                raise ValueError(
                    f"{file!r} must be a relative path to a .py file inside the pack"
                )
            if path.as_posix() in seen:
                raise ValueError(f"{file!r} is listed twice")
            seen.add(path.as_posix())
        return files


def load_manifest(directory: Path) -> PackManifest:
    """Read and validate the manifest of the pack in ``directory``.

    Raises :class:`PackError` with a readable message on any problem.
    """
    path = directory / MANIFEST_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PackError(f"{directory} is not a pack: it has no {MANIFEST_NAME}") from None
    except OSError as exc:
        raise PackError(f"cannot read {path}: {exc.strerror or exc}") from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PackError(f"invalid YAML in {path}: {_yaml_detail(exc)}") from exc
    if not isinstance(data, dict):
        raise PackError(
            f"invalid pack manifest {path}: the top level must be a mapping "
            f"(with a 'name' and a 'version'), not {_describe(data)}"
        )

    try:
        return PackManifest.model_validate(data)
    except ValidationError as exc:
        lines = [f"invalid pack manifest {path}:"]
        for error in exc.errors():
            message = error["msg"].removeprefix("Value error, ")
            lines.append(f"  {_where(error['loc'])}: {message}")
        raise PackError("\n".join(lines)) from exc


def _describe(data: object) -> str:
    return "an empty file" if data is None else f"a {type(data).__name__}"


def _yaml_detail(exc: yaml.YAMLError) -> str:
    problem = getattr(exc, "problem", None) or str(exc)
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return problem
    return f"{problem} (line {mark.line + 1}, column {mark.column + 1})"


def _where(loc: tuple[int | str, ...]) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out
