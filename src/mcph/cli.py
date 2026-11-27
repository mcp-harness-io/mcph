# src/mcph/cli.py
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from mcph import __version__
from mcph.config import ConfigError, load_config
from mcph.doctor import format_report, run_doctor
from mcph.mcp_client import DEFAULT_TIMEOUT_SECONDS

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
            help="Path to the mcph.yaml config file [default: ./mcph.yaml].",
        ),
    ] = None,
) -> None:
    ctx.obj = AppState(config_path=config)


@app.command()
def version() -> None:
    """Print the installed mcph version."""
    typer.echo(f"mcph {__version__}")


@app.command()
def doctor(
    ctx: typer.Context,
    timeout: Annotated[
        float,
        typer.Option(
            min=0.1,
            help="Seconds to wait for each server to start and answer.",
        ),
    ] = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Start the configured MCP servers and check that they respond.

    Exits with 1 if any server fails, and with 2 if the config is invalid.
    """
    state = ctx.ensure_object(AppState)
    try:
        config = load_config(state.config_path)
    except ConfigError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(2) from None

    report = asyncio.run(run_doctor(config, timeout=timeout))
    typer.echo(format_report(report, config_path=config.path))
    raise typer.Exit(0 if report.ok else 1)


@app.command()
def proxy() -> None:
    """Transparent stdio/HTTP proxy to record real MCP client traffic."""
    typer.echo("proxy: coming in v0.1.0")


@app.command()
def eval() -> None:
    """Execute mcpchecker task suites and score traces with an LLM judge."""
    typer.echo("eval: coming in v0.1.0")
