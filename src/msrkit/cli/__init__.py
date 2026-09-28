"""MSR-Kit CLI: command-line interface for grey literature mining.

The commands live in modules of this package, by stage of the study:

    collect     sources, validate, plan, run, normalize, fetch
    evidence    enrich, extract
    review      screen (export/import/status), coding (export/sample/import/status),
                agreement
    validation  precision (sample/score), recall
    corpus      prisma, dedupe, stats, export
    menu        interactive menu

`app`, `DATA_DIR` and `DEFAULT_PROTOCOL` live here; command modules read the
two paths from this module at call time (`_cli.DATA_DIR`), so tests and
embedding code can override them in one place.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from msrkit import __version__

# Ensure UTF-8 output on Windows consoles to avoid charmap encoding errors
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(
    name="msrkit",
    help="MSR-Kit: Mining grey literature through official APIs.",
    epilog="💡 Dica: Digite 'msrkit menu' para navegar de forma interativa com menu visual.",
    no_args_is_help=True,
)

# Default data directory and protocol
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = Path("data") if Path("data").exists() else (PROJECT_ROOT / "data")
DEFAULT_PROTOCOL = (
    "protocols/v0_rag_agents_testing.yaml"
    if Path("protocols/v0_rag_agents_testing.yaml").exists()
    else str(PROJECT_ROOT / "protocols" / "v0_rag_agents_testing.yaml")
)


def _version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        console.print(f"msrkit {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None,
        "--version",
        "-V",
        help="Show version",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """MSR-Kit: Mining grey literature through official APIs."""


# Register the commands (order = order in --help). Imported last: the command
# modules read DATA_DIR and DEFAULT_PROTOCOL from this module at call time, so
# tests can monkeypatch msrkit.cli.DATA_DIR.
from msrkit.cli import collect  # noqa: E402, F401, I001
from msrkit.cli import evidence  # noqa: E402, F401
from msrkit.cli import review  # noqa: E402, F401
from msrkit.cli import validation  # noqa: E402, F401
from msrkit.cli import corpus  # noqa: E402, F401
from msrkit.cli import menu  # noqa: E402, F401
from msrkit.cli._common import (  # noqa: E402, F401
    _tag_concepts,
    _tag_term_languages,
    console,
)
from msrkit.cli.collect import run  # noqa: E402, F401
from msrkit.cli.menu import _toggle_source_in_protocol  # noqa: E402, F401
