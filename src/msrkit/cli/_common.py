"""Shared helpers of the CLI commands (console, run lookup, loaders)."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.console import Console

from msrkit import cli as _cli

if TYPE_CHECKING:
    from msrkit.adapters.base import BaseAdapter

app = typer.Typer(
    name="msrkit",
    help="MSR-Kit: Mining grey literature through official APIs.",
    epilog="💡 Dica: Digite 'msrkit menu' para navegar de forma interativa com menu visual.",
    no_args_is_help=True,
)
console = Console()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_PROTOCOL = (
    "protocols/v0_rag_agents_testing.yaml"
    if Path("protocols/v0_rag_agents_testing.yaml").exists()
    else str(PROJECT_ROOT / "protocols" / "v0_rag_agents_testing.yaml")
)


def _get_latest_run_id() -> str | None:
    """Find the most recent run ID in _cli.DATA_DIR/runs or _cli.DATA_DIR/items."""
    runs_dir = _cli.DATA_DIR / "runs"
    if runs_dir.exists():
        subdirs = [p for p in runs_dir.iterdir() if p.is_dir()]
        if subdirs:
            return max(subdirs, key=lambda p: p.stat().st_mtime).name

    items_dir = _cli.DATA_DIR / "items"
    if items_dir.exists():
        subdirs = [p for p in items_dir.iterdir() if p.is_dir()]
        if subdirs:
            return max(subdirs, key=lambda p: p.stat().st_mtime).name

    return None


def _setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _get_registry() -> dict[str, type[BaseAdapter]]:
    """Import and return the adapter registry."""
    from msrkit.registry import all_adapters, discover_adapters

    discover_adapters()
    return all_adapters()


def _tag_concepts(item: Any, concepts: dict[str, list[str]]) -> None:
    """Label the item with every concept group whose lexicon matches its text."""
    if not concepts:
        return
    from msrkit.keywords import match_terms

    item.concepts = [
        name
        for name, lexicon in concepts.items()
        if match_terms(
            lexicon, title=item.title, body=item.body, tags=item.tech.tags, path=item.tech.path
        )
    ]


def _tag_term_languages(item: Any, term_langs: dict[str, str]) -> None:
    """Label each term hit with the language of its lexicon (language stratum)."""
    for hit in item.matched_terms:
        hit.lang = term_langs.get(hit.term, hit.lang)


def _unwrap(val: object) -> Any:
    """Unwrap Typer default parameter if called directly from Python code."""
    from typer.models import ArgumentInfo, OptionInfo

    if isinstance(val, (OptionInfo, ArgumentInfo)):
        return val.default
    return val


def _load_run_protocol(run_id: str, protocol: str | None) -> Any:
    """Protocol given explicitly, or the one recorded in the run manifest (None if absent)."""
    from msrkit.config import load_protocol

    path = protocol
    if not path:
        from msrkit.provenance import load_manifest

        with contextlib.suppress(FileNotFoundError):
            path = load_manifest(_cli.DATA_DIR, run_id).protocol_path
    if path and Path(path).exists():
        return load_protocol(path)
    return None


def _load_repo_signals(run_id: str) -> dict[str, Any]:
    from msrkit.enrich import RepoSignals

    path = _cli.DATA_DIR / "enrich" / run_id / "github_repos.jsonl"
    signals: dict[str, Any] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                sig = RepoSignals.model_validate_json(line)
                signals[sig.repo] = sig
    return signals


def _resolve_run(run_id: str | None) -> str:
    run_id = run_id or _get_latest_run_id()
    if not run_id:
        console.print("[red]✗ No runs found in data directory.[/red]")
        raise typer.Exit(1)
    return run_id


def _screening_criteria(run_id: str, protocol: str | None) -> Any:
    from msrkit.screening import ScreeningCriteria

    proto = _load_run_protocol(run_id, protocol)
    return proto.screening if proto is not None else ScreeningCriteria()


def _load_detections(run_id: str) -> list[Any]:
    from msrkit.extract import Detection

    path = _cli.DATA_DIR / "extract" / run_id / "detections.jsonl"
    if not path.exists():
        return []
    return [
        Detection.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _cell(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(value)
    return "" if value is None else str(value)
