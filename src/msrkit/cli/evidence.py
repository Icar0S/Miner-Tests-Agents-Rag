"""Evidence commands: repository enrichment and tool/method extraction."""

from __future__ import annotations

import typer
from rich.table import Table

from msrkit import cli as _cli
from msrkit.cli import app
from msrkit.cli._common import (
    _get_latest_run_id,
    _load_repo_signals,
    _load_run_protocol,
    _setup_logging,
    _unwrap,
    console,
)


@app.command()
def enrich(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    limit: int | None = typer.Option(None, "--limit", "-l", help="Max repositories to enrich"),
    protocol: str | None = typer.Option(
        None, "--protocol", "-p", help="Protocol (defaults to the one in the run manifest)"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Read tree, CI workflows, manifests, contributors and commit months of GitHub repos.

    Signals go to data/enrich/<run_id>/github_repos.jsonl and into the items
    (tech.has_ci, has_tests, contributors, active_months). Resumable.
    """
    run_id = _unwrap(run_id)
    limit = _unwrap(limit)
    protocol = _unwrap(protocol)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    from datetime import date as date_type

    from msrkit.adapters.github import GitHubAdapter
    from msrkit.enrich import GitHubEnricher, RepoSignals, apply_signals, repo_of
    from msrkit.governor import Governor
    from msrkit.storage import ItemStorage

    if not run_id:
        run_id = _get_latest_run_id()
        if not run_id:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)

    since = until = None
    config_files: set[str] = set()
    proto = _load_run_protocol(run_id, protocol)
    if proto is not None:
        since = date_type.fromisoformat(proto.window.since)
        until = date_type.fromisoformat(proto.window.until)
        if proto.gazetteer_data:
            config_files = {c for t in proto.gazetteer_data.tools for c in t.config_files}

    storage = ItemStorage(_cli.DATA_DIR)
    items = storage.read_items(run_id)
    branches: dict[str, str | None] = {}
    for it in items:
        repo = repo_of(it)
        if repo:
            branches.setdefault(repo, None)

    out_path = _cli.DATA_DIR / "enrich" / run_id / "github_repos.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    signals: dict[str, RepoSignals] = _load_repo_signals(run_id)

    todo = [r for r in branches if r not in signals]
    if limit is not None:
        todo = todo[:limit]
    console.print(
        f"[bold]Run:[/bold] {run_id} · {len(branches)} repositories, "
        f"{len(signals)} already enriched, {len(todo)} to go"
    )

    adapter = GitHubAdapter(
        governor=Governor(
            "github", GitHubAdapter.effective_rate_limit(), state_dir=_cli.DATA_DIR / "governor"
        )
    )
    enricher = GitHubEnricher(adapter, config_files=config_files, since=since, until=until)
    errors = 0
    try:
        with open(out_path, "a", encoding="utf-8") as fh:
            for repo in todo:
                sig = enricher.enrich(repo)
                signals[repo] = sig
                errors += sig.error is not None
                fh.write(sig.model_dump_json() + "\n")
                fh.flush()
    finally:
        adapter.close()

    updated = apply_signals(items, signals)
    storage.rewrite_items(items, run_id)
    deduped_path = _cli.DATA_DIR / "items" / run_id / "items_deduped.jsonl"
    if deduped_path.exists():
        deduped = storage.read_items(run_id, prefer_deduped=True)
        apply_signals(deduped, signals)
        storage.rewrite_items(deduped, run_id, deduped=True)

    console.print(
        f"[green]✓ Enriched {len(todo)} repositories ({errors} with errors); "
        f"{updated} items updated.[/green] Signals: {out_path}"
    )


@app.command()
def extract(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(
        None, "--protocol", "-p", help="Protocol (defaults to the one in the run manifest)"
    ),
    top: int = typer.Option(25, "--top", help="Rows shown in the summary table"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Detect gazetteer tools and methods with evidence level N1/N2/N3 (§3.4, §12.1).

    Uses item text, code search hits and, when available, `msrkit enrich` signals.
    Writes data/extract/<run_id>/detections.jsonl and detections.csv.
    """
    run_id = _unwrap(run_id)
    protocol = _unwrap(protocol)
    top = _unwrap(top)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    import csv

    from msrkit.extract import Extractor, summarize
    from msrkit.storage import ItemStorage

    if not run_id:
        run_id = _get_latest_run_id()
        if not run_id:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)

    proto = _load_run_protocol(run_id, protocol)
    if proto is None or proto.gazetteer_data is None:
        console.print("[red]✗ The protocol has no gazetteer (set `gazetteer:`).[/red]")
        raise typer.Exit(1)

    items = ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True)
    signals = _load_repo_signals(run_id)
    context_terms = [*proto.all_terms(), *(t for lex in proto.concepts.values() for t in lex)]
    extractor = Extractor(proto.gazetteer_data, context_terms=context_terms)
    detections = extractor.run(items, signals)

    out_dir = _cli.DATA_DIR / "extract" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "detections.jsonl", "w", encoding="utf-8") as fh:
        for d in detections:
            fh.write(d.model_dump_json() + "\n")
    fields = list(detections[0].model_dump()) if detections else []
    with open(out_dir / "detections.csv", "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter=";")
        if fields:
            writer.writeheader()
            writer.writerows(d.model_dump() for d in detections)

    summary = summarize(detections)
    table = Table(title=f"Detections by evidence level — {run_id}")
    for col in ("Entry", "N1", "N2", "N3"):
        table.add_column(col)
    ranked = sorted(summary.items(), key=lambda kv: (-kv[1]["N3"], -kv[1]["N2"], -kv[1]["N1"]))
    for entry, row in ranked[:top]:
        table.add_row(entry, str(row["N1"]), str(row["N2"]), str(row["N3"]))
    console.print(table)
    console.print(
        f"[green]✓ {len(detections)} detections from {len(items)} items and "
        f"{len(signals)} enriched repositories.[/green] Files: {out_dir}"
    )
    if extractor.dropped_ambiguous:
        console.print(
            f"[dim]{extractor.dropped_ambiguous} mention(s) of ambiguous tool names dropped "
            "(no lexicon context and no structural signal, §12.2).[/dim]"
        )
