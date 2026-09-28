"""Corpus commands: PRISMA flow, deduplication, statistics, export."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import typer
from rich.table import Table

from msrkit import cli as _cli
from msrkit.cli import app
from msrkit.cli._common import (
    _get_latest_run_id,
    _get_registry,
    _resolve_run,
    _setup_logging,
    _unwrap,
    console,
)


@app.command()
def prisma(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
) -> None:
    """PRISMA 2020 flow of a run from its manifest, discards, dedupe and screening (RF8).

    Writes data/reports/<run_id>/prisma.json and prisma.md (with a Mermaid diagram).
    """
    from msrkit.coding import load_codings
    from msrkit.prisma import build_flow, render_markdown
    from msrkit.provenance import load_manifest
    from msrkit.screening import load_decisions
    from msrkit.storage import ItemStorage

    run_id = _resolve_run(_unwrap(run_id))
    storage = ItemStorage(_cli.DATA_DIR)
    manifest = None
    with contextlib.suppress(FileNotFoundError):
        manifest = load_manifest(_cli.DATA_DIR, run_id)
    report_path = _cli.DATA_DIR / "items" / run_id / "dedupe_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
    flow = build_flow(
        run_id,
        manifest,
        storage.read_items(run_id),
        storage.read_items(run_id, prefer_deduped=True),
        report,
        _cli.DATA_DIR / "runs" / run_id / "discarded.jsonl",
        load_decisions(_cli.DATA_DIR, run_id),
        {item_id for (item_id, _c) in load_codings(_cli.DATA_DIR, run_id)},
    )
    out_dir = _cli.DATA_DIR / "reports" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "prisma.json").write_text(flow.model_dump_json(indent=2), encoding="utf-8")
    markdown = render_markdown(flow)
    (out_dir / "prisma.md").write_text(markdown, encoding="utf-8")
    dup = "n/a" if flow.duplicates is None else sum(flow.duplicates.values())
    console.print(
        f"Identified {flow.total_identified} → filtered {sum(flow.filtered.values())}, "
        f"duplicates {dup} → screened {flow.screened} → excluded {flow.excluded}, "
        f"uncertain {flow.uncertain}, pending {flow.pending} → included {flow.included} "
        f"(coded {flow.coded})"
    )
    if manifest is None:
        console.print("[yellow]⚠ No manifest: identification counts come from items only.[/yellow]")
    console.print(f"[green]✓ PRISMA flow written to {out_dir}[/green]")


@app.command(name="dedupe")
def dedupe(
    run_id: str | None = typer.Option(
        None, "--run", help="Run ID to deduplicate (defaults to latest)"
    ),
    all_runs: bool = typer.Option(
        False, "--all", "-a", help="Consolidate and deduplicate across all historical runs"
    ),
    near_threshold: float = typer.Option(
        0.85,
        "--near-threshold",
        help="Estimated Jaccard for near-duplicates (pass 3; calibrate in the pilot, 0 disables)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Deduplicate items for a run or across all runs."""
    run_id = _unwrap(run_id)
    all_runs = _unwrap(all_runs)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    from msrkit.dedupe import deduplicate_report
    from msrkit.storage import ItemStorage

    item_storage = ItemStorage(_cli.DATA_DIR)

    if all_runs:
        runs = item_storage.list_runs()
        if not runs:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)
        console.print(f"[bold]Deduplicating across {len(runs)} historical runs...[/bold]")
        all_items = []
        for r in runs:
            all_items.extend(item_storage.read_items(r, prefer_deduped=False))
        items = all_items
        target_dir = _cli.DATA_DIR / "items" / "consolidated"
    else:
        if not run_id:
            run_id = _get_latest_run_id()
            if not run_id:
                console.print("[red]✗ No runs found in data directory.[/red]")
                raise typer.Exit(1)
            console.print(f"[dim]Auto-selected latest run:[/dim] [cyan]{run_id}[/cyan]")
        console.print(f"[bold]Deduplicating run:[/bold] {run_id}")
        items = item_storage.read_items(run_id, prefer_deduped=False)
        target_dir = _cli.DATA_DIR / "items" / run_id

    if not items:
        console.print("[yellow]No items found.[/yellow]")
        return

    threshold = _unwrap(near_threshold)
    report = deduplicate_report(items, near_threshold=threshold if threshold else None)
    unique, duplicates = report.unique, report.duplicates
    counts = report.counts()
    console.print(f"  Input: {len(items)}")
    console.print(f"  Unique: {len(unique)}")
    console.print(
        f"  Duplicates removed: {len(duplicates)} "
        f"(url: {counts['url']}, content: {counts['content']}, near: {counts['near']})"
    )

    # Save deduplicated items
    target_dir.mkdir(parents=True, exist_ok=True)
    deduped_path = target_dir / "items_deduped.jsonl"
    with open(deduped_path, "w", encoding="utf-8") as f:
        for item in unique:
            f.write(item.model_dump_json() + "\n")

    report_path = target_dir / "dedupe_report.json"
    report_path.write_text(
        json.dumps(
            {
                "input": len(items),
                "unique": len(unique),
                "near_threshold": threshold or None,
                "removed": counts,
                "reasons": report.reasons,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    console.print(f"[green]✓ Saved to {deduped_path}[/green] (reasons: {report_path.name})")


@app.command()
def stats(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    all_runs: bool = typer.Option(
        False, "--all", "-a", help="Show consolidated corpus statistics across all historical runs"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show collection statistics for a run or the consolidated corpus."""
    run_id = _unwrap(run_id)
    all_runs = _unwrap(all_runs)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    from msrkit.storage import ItemStorage

    item_storage = ItemStorage(_cli.DATA_DIR)

    if all_runs:
        runs = item_storage.list_runs()
        if not runs:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)

        from msrkit.dedupe import deduplicate

        all_items = []
        for r in runs:
            all_items.extend(item_storage.read_items(r, prefer_deduped=True))

        if not all_items:
            console.print("[yellow]No items found across runs.[/yellow]")
            return

        unique_items, duplicates = deduplicate(all_items)
        hist_by_source: dict[str, int] = {}
        for it in unique_items:
            hist_by_source[it.source] = hist_by_source.get(it.source, 0) + 1

        hist_table = Table(
            title=f"Consolidated Historical Corpus ({len(runs)} runs)",
            show_lines=True,
        )
        hist_table.add_column("Source", style="bold")
        hist_table.add_column("Unique Items", justify="right")
        hist_table.add_column("Corpus Share", justify="right")

        total_hist = len(unique_items)
        for src, count in sorted(hist_by_source.items()):
            share = (count / total_hist * 100) if total_hist > 0 else 0
            hist_table.add_row(src, str(count), f"{share:.1f}%")

        hist_table.add_section()
        hist_table.add_row(
            "[bold]Total Consolidated[/bold]",
            f"[bold green]{total_hist}[/bold green]",
            "100.0%",
        )
        console.print(hist_table)
        console.print(
            f"\n[dim]Total across history: {len(all_items)} raw items collected | "
            f"{len(duplicates)} duplicates removed[/dim]"
        )
        return

    if not run_id:
        run_id = _get_latest_run_id()
        if not run_id:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)
        console.print(f"[dim]Auto-selected latest run:[/dim] [cyan]{run_id}[/cyan]")

    from msrkit.provenance import load_manifest

    try:
        manifest = load_manifest(_cli.DATA_DIR, run_id)
    except FileNotFoundError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1) from None

    table = Table(title=f"Run Statistics: {run_id}", show_lines=True)
    table.add_column("Source", style="bold")
    table.add_column("Status")
    table.add_column("Queries")
    table.add_column("Items")
    table.add_column("Requests")
    table.add_column("Truncated")

    total_items = 0
    total_requests = 0

    for source in manifest.sources:
        status_color = {
            "OK": "green",
            "DEGRADED": "yellow",
            "UNSUPPORTED": "red",
        }.get(source.availability.status, "white")

        items = sum(q.items for q in source.queries)
        requests = sum(q.requests for q in source.queries)
        truncated = any(q.truncated for q in source.queries)
        reason_counts: dict[str, int] = {}
        for q in source.queries:
            for reason in q.truncation_reasons:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        reason_text = ", ".join(f"{r}×{n}" for r, n in sorted(reason_counts.items()))
        total_items += items
        total_requests += requests

        table.add_row(
            source.name,
            f"[{status_color}]{source.availability.status}[/{status_color}]",
            str(len(source.queries)),
            str(items),
            str(requests),
            (f"[red]Yes[/red] ({reason_text})" if reason_text else "[red]Yes[/red]")
            if truncated
            else "[green]No[/green]",
        )

    console.print(table)
    console.print(f"\n[bold]Total items:[/bold] {total_items}")
    console.print(f"[bold]Total requests:[/bold] {total_requests}")

    # Check for items file of this run
    items_list = item_storage.read_items(run_id, prefer_deduped=True)
    if items_list:
        by_source: dict[str, int] = {}
        for item in items_list:
            by_source[item.source] = by_source.get(item.source, 0) + 1

        console.print("\n[bold]Items on disk by source:[/bold]")
        for src, count in sorted(by_source.items()):
            console.print(f"  {src}: {count}")

        items_hash = item_storage.items_hash(run_id)
        console.print(f"\n[bold]Items file SHA-256:[/bold] {items_hash}")

    # Historical Consolidated Overview across all runs
    runs = item_storage.list_runs()
    if len(runs) > 1:
        from msrkit.dedupe import deduplicate

        all_items = []
        for r in runs:
            all_items.extend(item_storage.read_items(r, prefer_deduped=True))

        if all_items:
            unique_items, duplicates = deduplicate(all_items)
            hist_by_source = {}
            for it in unique_items:
                hist_by_source[it.source] = hist_by_source.get(it.source, 0) + 1

            hist_table = Table(
                title=f"\nConsolidated Historical Corpus ({len(runs)} runs)",
                show_lines=True,
            )
            hist_table.add_column("Source", style="bold")
            hist_table.add_column("Unique Items", justify="right")
            hist_table.add_column("Corpus Share", justify="right")

            total_hist = len(unique_items)
            for src, count in sorted(hist_by_source.items()):
                share = (count / total_hist * 100) if total_hist > 0 else 0
                hist_table.add_row(src, str(count), f"{share:.1f}%")

            hist_table.add_section()
            hist_table.add_row(
                "[bold]Total Consolidated[/bold]",
                f"[bold green]{total_hist}[/bold green]",
                "100.0%",
            )
            console.print(hist_table)
            console.print(
                f"[dim]Historical total: {len(all_items)} raw items collected across runs | "
                f"{len(duplicates)} duplicates removed[/dim]"
            )


@app.command()
def export(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    all_runs: bool = typer.Option(
        False, "--all", "-a", help="Consolidate and export items from all historical runs"
    ),
    fmt: str = typer.Option("jsonl", "--format", "-f", help="Output format: csv, jsonl, duckdb"),
    delimiter: str = typer.Option(
        ";",
        "--delimiter",
        "-d",
        help="Delimiter for CSV export (default ';' for Excel compatibility, or ',')",
    ),
    include_body: bool = typer.Option(False, "--include-body", help="Include body text in export"),
    output: str | None = typer.Option(None, "--output", "-o", help="Output file path"),
    raw: bool = typer.Option(False, "--raw", help="Export raw items instead of deduplicated items"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Export collected items."""
    run_id = _unwrap(run_id)
    all_runs = _unwrap(all_runs)
    fmt = _unwrap(fmt)
    delimiter = _unwrap(delimiter)
    include_body = _unwrap(include_body)
    output = _unwrap(output)
    raw = _unwrap(raw)
    verbose = _unwrap(verbose)
    _setup_logging(verbose)

    from msrkit.storage import ItemStorage

    item_storage = ItemStorage(_cli.DATA_DIR)

    if all_runs:
        runs = item_storage.list_runs()
        if not runs:
            console.print("[red]✗ No runs found in data directory.[/red]")
            raise typer.Exit(1)
        console.print(f"[bold]Consolidating items from {len(runs)} historical runs...[/bold]")
        all_items = []
        for r in runs:
            all_items.extend(item_storage.read_items(r, prefer_deduped=not raw))
        if not raw:
            from msrkit.dedupe import deduplicate

            items, dups = deduplicate(all_items)
            console.print(
                f"  [green]Total consolidado:[/green] {len(items)} únicos "
                f"([dim]{len(dups)} duplicatas removidas[/dim])"
            )
        else:
            items = all_items
            console.print(f"  [green]Total consolidado bruto:[/green] {len(items)} achados")
        target_name = "consolidated"
    else:
        if not run_id:
            run_id = _get_latest_run_id()
            if not run_id:
                console.print("[red]✗ No runs found in data directory.[/red]")
                raise typer.Exit(1)
            console.print(f"[dim]Auto-selected latest run:[/dim] [cyan]{run_id}[/cyan]")
        items = item_storage.read_items(run_id, prefer_deduped=not raw)
        target_name = run_id

    if not items:
        console.print("[yellow]No items found to export.[/yellow]")
        return

    # Enforce redistribution policy
    registry = _get_registry()
    if include_body:
        sources_in_items = {item.source for item in items}
        violating = [
            s
            for s in sorted(sources_in_items)
            if s in registry and registry[s].policy.redistribution == "metadata_only"
        ]
        if violating:
            console.print(
                f"[red]✗ Cannot export body: source(s) {', '.join(violating)} "
                f"have redistribution policy 'metadata_only'.[/red]"
            )
            raise typer.Exit(1)

    out_path = output or str(_cli.DATA_DIR / "export" / target_name / f"items.{fmt}")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        if fmt == "jsonl":
            with open(out_path, "w", encoding="utf-8") as f:
                for item in items:
                    data = item.model_dump(mode="json")
                    if not include_body:
                        data.pop("body", None)
                    f.write(json.dumps(data, ensure_ascii=False) + "\n")
        elif fmt == "csv":
            import csv

            fields = [
                "id",
                "source",
                "kind",
                "url",
                "title",
                "author_handle",
                "created_at",
                "updated_at",
                "matched_terms",
                "matched_languages",
                "concepts",
                "stars",
                "votes",
                "tags",
            ]
            if include_body:
                fields.append("body")

            with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(
                    f, fieldnames=fields, delimiter=delimiter, extrasaction="ignore"
                )
                writer.writeheader()
                for item in items:
                    row = item.model_dump(mode="json")
                    row["url"] = str(item.url)
                    row["kind"] = item.kind.value
                    row["matched_terms"] = (
                        ", ".join(sorted(set(hit.term for hit in item.matched_terms)))
                        if item.matched_terms
                        else ""
                    )
                    row["matched_languages"] = ", ".join(
                        sorted({hit.lang for hit in item.matched_terms if hit.lang})
                    )
                    row["concepts"] = ", ".join(item.concepts)
                    row["stars"] = (
                        item.engagement.stars
                        if (item.engagement and item.engagement.stars is not None)
                        else ""
                    )
                    row["votes"] = (
                        item.engagement.votes
                        if (item.engagement and item.engagement.votes is not None)
                        else ""
                    )
                    row["tags"] = (
                        ", ".join(item.tech.tags) if (item.tech and item.tech.tags) else ""
                    )
                    writer.writerow({k: row.get(k) for k in fields})
        elif fmt == "duckdb":
            from msrkit.storage import DuckDBStorage

            db_path = Path(out_path).with_suffix(".duckdb")
            db = DuckDBStorage(db_path)
            inserted = db.ingest_items(items, include_body=include_body)
            db.close()
            console.print(f"[green]✓ Inserted {inserted} items into {db_path}[/green]")
            return
        else:
            console.print(f"[red]Unknown format: {fmt}[/red]")
            raise typer.Exit(1)
    except PermissionError:
        console.print(
            f"[red]✗ Permissão negada ao salvar '{out_path}'.\n"
            "O arquivo pode estar aberto em outro aplicativo (como Excel). "
            "Feche o arquivo e tente novamente.[/red]"
        )
        raise typer.Exit(1) from None

    console.print(f"[green]✓ Exported {len(items)} items to {out_path}[/green]")
