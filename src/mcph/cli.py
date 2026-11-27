# src/mcph/cli.py
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, TypeGuard

import typer

from mcph import __version__
from mcph.config import (
    Config,
    ConfigError,
    HttpServerConfig,
    OAuthAuthConfig,
    ServerConfig,
    default_config_path,
    load_config,
)
from mcph.doctor import format_report, format_rules, run_doctor
from mcph.extensions import (
    Registry,
    format_packs,
    format_verification,
    load_extensions,
    load_packs,
    verify_pack,
)
from mcph.mcp_client import DEFAULT_TIMEOUT_SECONDS
from mcph.oauth_login import DEFAULT_LOGIN_TIMEOUT_SECONDS, LoginError
from mcph.oauth_login import login as sign_in
from mcph.token_store import TokenStore, TokenStoreError

app = typer.Typer(
    name="mcph",
    help="MCP Harness — diagnostics, real-client proxy, and LLM-as-a-judge evals for MCP servers.",
    no_args_is_help=True,
)


@dataclass
class AppState:
    """Global options shared by all commands."""

    config_path: Path | None = None


@app.callback()
def main(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to the mcph.yaml config file (default: ./mcph.yaml).",
        ),
    ] = None,
) -> None:
    ctx.obj = AppState(config_path=config)


@app.command()
def version() -> None:
    """Print the installed mcph version."""
    typer.echo(f"mcph {__version__}")


# Not no_args_is_help: with click < 8.2 that exits with 0, and a CI job that still
# calls a bare `mcph doctor` must fail instead of passing without checking anything.
doctor_app = typer.Typer(help="Check the MCP servers in mcph.yaml.")
app.add_typer(doctor_app, name="doctor")


@doctor_app.command()
def run(
    ctx: typer.Context,
    timeout: Annotated[
        float,
        typer.Option(
            min=0.1,
            help="Seconds to wait for each server to start and answer.",
        ),
    ] = DEFAULT_TIMEOUT_SECONDS,
    strict: Annotated[
        bool,
        typer.Option(
            "--strict", help="Also exit with 1 if there are warnings (for CI)."
        ),
    ] = False,
) -> None:
    """Check that the configured MCP servers respond.

    Starts stdio servers and connects to http servers (which must already be
    running), then checks what each exposes against the rules of the packs under
    `extends` in mcph.yaml. Without any pack there is no rule.
    Exits with 1 if a server fails (or, with --strict, has warnings), and with 2
    if the config is invalid.
    """
    config = _load_config_or_exit(ctx)
    registry = _load_extensions_or_exit(config)
    report = asyncio.run(run_doctor(config, rules=registry.rules, timeout=timeout))
    typer.echo(format_report(report, config_path=config.path))
    raise typer.Exit(0 if report.passed(strict=strict) else 1)


@doctor_app.command()
def rules(ctx: typer.Context) -> None:
    """List the rules of the packs under `extends`, without starting any server.

    Shows every rule with its severity: the default, or what `doctor.rules` in
    mcph.yaml made of it (the default is shown next to a changed one). Without
    a mcph.yaml in the current directory no pack is loaded, so there is no rule.
    """
    config = _load_config_if_present(ctx)
    if config is None:
        typer.echo(format_rules([], {}, config_path=None))
        return
    registry = _load_extensions_or_exit(config)
    typer.echo(format_rules(registry.packs, config.doctor.rules, config_path=config.path))


pack_app = typer.Typer(
    help="Inspect and check the packs that add rules.", no_args_is_help=True
)
app.add_typer(pack_app, name="pack")


@pack_app.command("list")
def list_packs(ctx: typer.Context) -> None:
    """List the packs under `extends` with the rules each one defines.

    Without a mcph.yaml in the current directory no pack is loaded.
    """
    config = _load_config_if_present(ctx)
    if config is None:
        typer.echo(format_packs([], config_path=None))
        return
    try:
        registry = load_packs(config)
    except ConfigError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from None
    typer.echo(format_packs(registry.packs, config_path=config.path))


@pack_app.command()
def verify(
    path: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            help="The directory of the pack (the one with mcph-pack.yaml).",
        ),
    ] = Path("."),
) -> None:
    """Check a pack: its manifest, its rule files and the rules they define.

    This imports the rule files, so it runs the Python of the pack. Exits with 1
    if the pack has problems (all of them are listed), and with 2 if PATH is not
    a directory.
    """
    report = verify_pack(Path(os.path.abspath(path)))
    typer.echo(format_verification(report, path=str(path)))
    raise typer.Exit(1 if report.problems else 0)


auth_app = typer.Typer(
    help="Sign in to HTTP servers that use OAuth (auth: {type: oauth}).",
    no_args_is_help=True,
)
app.add_typer(auth_app, name="auth")

ServerArgument = Annotated[
    str, typer.Argument(help="Name of the server in mcph.yaml.", show_default=False)
]


@auth_app.command()
def login(
    ctx: typer.Context,
    server: ServerArgument,
    timeout: Annotated[
        float,
        typer.Option(
            min=1.0, help="Seconds to wait for the sign-in in the browser to finish."
        ),
    ] = DEFAULT_LOGIN_TIMEOUT_SECONDS,
) -> None:
    """Sign in to an OAuth server through the browser and store its token.

    The token is kept in a file only you can read, and `mcph doctor run` uses it.
    It usually expires after a few minutes; run this again when it does.
    """
    target = _oauth_server_or_exit(ctx, server)
    typer.echo(f"Signing in to {target.name} ({target.url})")

    def announce(url: str) -> None:
        typer.echo(f"Opening your browser. If it does not open, visit:\n  {url}")

    try:
        token = asyncio.run(
            sign_in(target, TokenStore(), announce=announce, timeout=timeout)
        )
    except (LoginError, TokenStoreError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"Logged in to {target.name}; the token {token.describe()}.")


@auth_app.command()
def status(
    ctx: typer.Context,
    server: Annotated[
        str | None,
        typer.Argument(help="Only this server (default: all OAuth servers)."),
    ] = None,
) -> None:
    """Show which OAuth servers are logged in and when their tokens expire."""
    if server is not None:
        servers = [_oauth_server_or_exit(ctx, server)]
    else:
        config = _load_config_or_exit(ctx)
        servers = [s for s in config.servers if _uses_oauth(s)]
    if not servers:
        typer.echo("No server in the config uses `auth: {type: oauth}`.")
        return

    store = TokenStore()
    width = max(len(s.name) for s in servers)
    for target in servers:
        try:
            token = asyncio.run(store.load(target))
        except TokenStoreError as exc:
            description = f"unreadable: {exc}"
        else:
            if token is None:
                description = "not logged in"
            else:
                description = f"logged in; the token {token.describe()}"
        typer.echo(f"{target.name:<{width}}  {description}")


@auth_app.command()
def logout(ctx: typer.Context, server: ServerArgument) -> None:
    """Delete the stored token of an OAuth server."""
    target = _oauth_server_or_exit(ctx, server)
    if TokenStore().delete(target):
        typer.echo(f"Logged out of {target.name}.")
    else:
        typer.echo(f"{target.name} was not logged in.")


def _load_config_or_exit(ctx: typer.Context) -> Config:
    state = ctx.ensure_object(AppState)
    try:
        return load_config(state.config_path)
    except ConfigError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from None


def _load_extensions_or_exit(config: Config) -> Registry:
    try:
        return load_extensions(config)
    except ConfigError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from None


def _load_config_if_present(ctx: typer.Context) -> Config | None:
    """The config; ``None`` if none was asked for and ``./mcph.yaml`` does not exist."""
    state = ctx.ensure_object(AppState)
    if state.config_path is None and not default_config_path().exists():
        return None
    return _load_config_or_exit(ctx)


def _uses_oauth(server: ServerConfig) -> TypeGuard[HttpServerConfig]:
    return isinstance(server, HttpServerConfig) and isinstance(
        server.auth, OAuthAuthConfig
    )


def _oauth_server_or_exit(ctx: typer.Context, name: str) -> HttpServerConfig:
    config = _load_config_or_exit(ctx)
    for server in config.servers:
        if server.name == name:
            break
    else:
        known = ", ".join(s.name for s in config.servers)
        typer.echo(
            f"Error: no server named {name!r} in {config.path} (known: {known})",
            err=True,
        )
        raise typer.Exit(2)
    if not _uses_oauth(server):
        typer.echo(
            f"Error: server {name!r} does not use OAuth login "
            "(it needs `transport: http` and `auth: {type: oauth}`)",
            err=True,
        )
        raise typer.Exit(2)
    return server


@app.command()
def proxy() -> None:
    """Transparent stdio/HTTP proxy to record real MCP client traffic (coming in 0.3.0)."""
    typer.echo("proxy: coming in 0.3.0")


@app.command()
def eval() -> None:
    """Execute mcpchecker task suites and score traces with an LLM judge (coming in 0.4.0)."""
    typer.echo("eval: coming in 0.4.0")
