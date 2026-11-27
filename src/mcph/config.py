"""Loading and validation of ``mcph.yaml``.

The config file declares the MCP servers a project works with: ``stdio``
servers that mcph starts itself, and ``http`` servers that are already running.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
)

DEFAULT_CONFIG_NAME = "mcph.yaml"
CONFIG_VERSION = "1"

_SERVER_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TRANSPORTS = ("stdio", "http")


class ConfigError(Exception):
    """The config file is missing, unreadable or invalid."""


class _StrictModel(BaseModel):
    # Reject unknown keys so typos (and options that are not supported yet)
    # fail loudly instead of being silently ignored.
    model_config = ConfigDict(extra="forbid")


class _ServerBase(_StrictModel):
    name: str
    primary: bool = False

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not _SERVER_NAME_RE.fullmatch(value):
            raise ValueError(
                "must start with a letter or digit and contain only letters, "
                "digits, '_' and '-'"
            )
        return value


class StdioServerConfig(_ServerBase):
    """A server that mcph starts as a subprocess and talks to over stdio."""

    transport: Literal["stdio"]
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class BearerAuthConfig(_StrictModel):
    """Send a bearer token that mcph reads from an environment variable.

    The token itself never goes into ``mcph.yaml``: only the variable's name.
    """

    type: Literal["bearer"]
    token_env: str

    @field_validator("token_env")
    @classmethod
    def _check_token_env(cls, value: str) -> str:
        if not _ENV_NAME_RE.fullmatch(value):
            raise ValueError(
                "must be an environment variable name: letters, digits and '_', "
                "not starting with a digit"
            )
        return value


class HttpServerConfig(_ServerBase):
    """A server that is already running and speaks Streamable HTTP."""

    transport: Literal["http"]
    url: str
    auth: BearerAuthConfig | None = None

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("must be an http:// or https:// URL")
        return value


ServerConfig = Annotated[
    StdioServerConfig | HttpServerConfig, Field(discriminator="transport")
]


class Config(_StrictModel):
    """The contents of ``mcph.yaml``."""

    version: Literal["1"] = CONFIG_VERSION
    servers: list[ServerConfig] = Field(min_length=1)

    _path: Path | None = PrivateAttr(default=None)

    @field_validator("version", mode="before")
    @classmethod
    def _coerce_version(cls, value: object) -> object:
        # YAML reads an unquoted ``version: 1`` as an int.
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value

    @field_validator("servers")
    @classmethod
    def _check_servers(cls, servers: list[ServerConfig]) -> list[ServerConfig]:
        seen: set[str] = set()
        for server in servers:
            if server.name in seen:
                raise ValueError(f"duplicate server name {server.name!r}")
            seen.add(server.name)
        primaries = [server.name for server in servers if server.primary]
        if len(primaries) > 1:
            raise ValueError(
                "only one server may set 'primary: true', found: "
                + ", ".join(primaries)
            )
        return servers

    @property
    def path(self) -> Path | None:
        """Absolute path of the config file, or ``None`` if built in code."""
        return self._path

    @property
    def base_dir(self) -> Path:
        """Directory containing the config file; server commands run from here.

        Falls back to the current directory for a config built in code.
        """
        return self._path.parent if self._path is not None else Path.cwd()

    @property
    def primary_server(self) -> ServerConfig:
        """The server marked ``primary: true``, or the first one."""
        return next((s for s in self.servers if s.primary), self.servers[0])


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Read and validate a config file.

    ``path`` defaults to ``mcph.yaml`` in the current working directory.
    Raises :class:`ConfigError` with a readable message on any problem.
    """
    config_path = Path(path) if path is not None else Path.cwd() / DEFAULT_CONFIG_NAME
    try:
        text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {config_path}") from None
    except OSError as exc:
        raise ConfigError(
            f"Cannot read config file {config_path}: {exc.strerror or exc}"
        ) from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {config_path}: {_yaml_detail(exc)}") from exc

    if data is None:  # empty file: let validation report what is missing
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(
            f"Invalid config {config_path}: the top level must be a mapping "
            f"(with a 'servers' list), not a {type(data).__name__}"
        )

    try:
        config = Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_validation_detail(config_path, exc)) from exc

    config._path = Path(os.path.abspath(config_path))
    return config


def _yaml_detail(exc: yaml.YAMLError) -> str:
    problem = getattr(exc, "problem", None) or str(exc)
    mark = getattr(exc, "problem_mark", None)
    if mark is None:
        return problem
    return f"{problem} (line {mark.line + 1}, column {mark.column + 1})"


def _validation_detail(path: Path, exc: ValidationError) -> str:
    lines = [f"Invalid config {path}:"]
    for error in exc.errors():
        loc = _without_union_tag(error["loc"])
        message = error["msg"].removeprefix("Value error, ")
        if error["type"] == "union_tag_not_found":
            loc, message = (*loc, "transport"), "Field required (stdio or http)"
        elif error["type"] == "union_tag_invalid":
            loc, message = (*loc, "transport"), "Input should be 'stdio' or 'http'"
        where = _format_loc(loc)
        lines.append(f"  {where}: {message}" if where else f"  {message}")
    return "\n".join(lines)


def _without_union_tag(loc: tuple[int | str, ...]) -> tuple[int | str, ...]:
    # For errors inside a server entry pydantic inserts the matched transport
    # ("stdio" or "http") after the list index; that is not a key in the YAML.
    if (
        len(loc) > 2
        and loc[0] == "servers"
        and isinstance(loc[1], int)
        and loc[2] in _TRANSPORTS
    ):
        return loc[:2] + loc[3:]
    return loc


def _format_loc(loc: tuple[int | str, ...]) -> str:
    out = ""
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += f".{part}" if out else str(part)
    return out
