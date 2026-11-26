# src/mcph/cli.py
import typer
from mcph import __version__

app = typer.Typer(
    name="mcph",
    help="MCP Harness — diagnostics, real-client proxy, and LLM-as-a-judge evals for MCP servers.",
    no_args_is_help=True,
)


@app.command()
def version() -> None:
    """Print the installed mcph version."""
    typer.echo(f"mcph {__version__}")


@app.command()
def doctor() -> None:
    """Run pre-flight schema, transport, and client compatibility checks."""
    typer.echo("doctor: coming in v0.1.0")


@app.command()
def proxy() -> None:
    """Transparent stdio/HTTP proxy to record real MCP client traffic."""
    typer.echo("proxy: coming in v0.1.0")


@app.command()
def eval() -> None:
    """Execute mcpchecker task suites and score traces with an LLM judge."""
    typer.echo("eval: coming in v0.1.0")
