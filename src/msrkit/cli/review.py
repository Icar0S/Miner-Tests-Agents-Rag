"""Manual review commands: screening, extraction form, inter-coder agreement."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from msrkit import cli as _cli
from msrkit.cli._common import (
    _cell,
    _load_detections,
    _load_run_protocol,
    _resolve_run,
    _screening_criteria,
    _unwrap,
    app,
    console,
)

screen_app = typer.Typer(help="Screening sheets: export, import decisions, status (§8, §11).")


app.add_typer(screen_app, name="screen")


@screen_app.command("export")
def screen_export(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    coder: str = typer.Option("", "--coder", help="Pre-fill the coder column"),
    output: str | None = typer.Option(None, "--output", "-o", help="Sheet path (.csv)"),
    pending_only: bool = typer.Option(
        True, "--pending-only/--all", help="Skip items this coder already decided"
    ),
    order: str = typer.Option(
        "relevance", "--order", help="relevance (fixed ranked batches) or collected"
    ),
    batch: list[int] = typer.Option(  # noqa: B008
        [], "--batch", "-b", help="Export only these batch numbers (repeatable)"
    ),
) -> None:
    """Write a screening sheet (CSV) with one column per eligibility criterion.

    By default items are ranked by relevance (matched terms, concepts, evidence
    level from `msrkit extract`, CI/tests) and cut into fixed batches over the
    whole run (§11), so batch numbers stay stable across coders and re-exports.
    """
    from msrkit.screening import Ranked, export_sheet, load_decisions, rank_items
    from msrkit.storage import ItemStorage

    run_id = _resolve_run(_unwrap(run_id))
    coder = _unwrap(coder)
    order = _unwrap(order)
    batches = set(_unwrap(batch) or [])
    if order not in ("relevance", "collected"):
        console.print("[red]✗ --order must be 'relevance' or 'collected'.[/red]")
        raise typer.Exit(1)
    criteria = _screening_criteria(run_id, _unwrap(protocol))
    items = ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True)
    detections = _load_detections(run_id) if order == "relevance" else []
    ranking = (
        rank_items(items, detections, criteria.batch_size)
        if order == "relevance"
        else {
            it.id: Ranked(
                item_id=it.id, rank=i + 1, batch=i // max(criteria.batch_size, 1) + 1, score=0
            )
            for i, it in enumerate(items)
        }
    )
    if _unwrap(pending_only) and coder:
        done = {item_id for (item_id, c) in load_decisions(_cli.DATA_DIR, run_id) if c == coder}
        items = [it for it in items if it.id not in done]
    if batches:
        items = [it for it in items if ranking[it.id].batch in batches]
    out = Path(
        _unwrap(output) or _cli.DATA_DIR / "screening" / run_id / f"sheet_{coder or 'blank'}.csv"
    )
    n = export_sheet(items, criteria, out, coder=coder, ranking=ranking)
    total_batches = max((r.batch for r in ranking.values()), default=0)
    console.print(
        f"[green]✓ {n} items written to {out}[/green] "
        f"(order: {order}; {total_batches} batches of {criteria.batch_size})"
    )
    if order == "relevance" and not detections:
        console.print(
            "[dim]No detections for this run: run `msrkit extract` first to rank by "
            "evidence level.[/dim]"
        )


@screen_app.command("import")
def screen_import(
    sheet: str = typer.Argument(..., help="Filled screening sheet (.csv)"),
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    coder: str = typer.Option("", "--coder", help="Coder for rows with an empty coder column"),
) -> None:
    """Validate a filled sheet and record its decisions (nothing is imported on error)."""
    from msrkit.screening import SheetError, append_decisions, import_sheet
    from msrkit.storage import ItemStorage

    run_id = _resolve_run(_unwrap(run_id))
    criteria = _screening_criteria(run_id, _unwrap(protocol))
    known = {it.id for it in ItemStorage(_cli.DATA_DIR).read_items(run_id)}
    try:
        decisions = import_sheet(Path(sheet), criteria, known, default_coder=_unwrap(coder))
    except SheetError as e:
        console.print(f"[red]✗ {e}[/red]")
        for problem in e.problems[:50]:
            console.print(f"  - {problem}")
        raise typer.Exit(1) from None
    path = append_decisions(_cli.DATA_DIR, run_id, decisions)
    console.print(f"[green]✓ {len(decisions)} decisions recorded in {path}[/green]")


@screen_app.command("status")
def screen_status(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
) -> None:
    """Decisions per coder and the resulting include/exclude/uncertain counts."""
    from msrkit.screening import final_decisions, load_decisions
    from msrkit.storage import ItemStorage

    run_id = _resolve_run(_unwrap(run_id))
    latest = load_decisions(_cli.DATA_DIR, run_id)
    total = len(ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True))
    table = Table(title=f"Screening — {run_id} ({total} items)")
    for col in ("Coder", "include", "exclude", "uncertain"):
        table.add_column(col)
    per_coder: dict[str, dict[str, int]] = {}
    for (_item, coder), d in latest.items():
        row = per_coder.setdefault(coder, {"include": 0, "exclude": 0, "uncertain": 0})
        row[d.decision] += 1
    for coder, row in sorted(per_coder.items()):
        table.add_row(coder, str(row["include"]), str(row["exclude"]), str(row["uncertain"]))
    final = final_decisions(latest)
    table.add_row(
        "[bold]final[/bold]",
        *(str(sum(v == k for v in final.values())) for k in ("include", "exclude", "uncertain")),
    )
    console.print(table)
    console.print(f"Pending (no decision): {total - len(final)}")


coding_app = typer.Typer(help="Extraction form (Annex A) and quality (§9): export, import, status.")


app.add_typer(coding_app, name="coding")


@coding_app.command("export")
def coding_export(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    coder: str = typer.Option("", "--coder", help="Pre-fill the coder column"),
    output: str | None = typer.Option(None, "--output", "-o", help="Sheet path (.csv)"),
    status: str = typer.Option(
        "include", "--status", help="Items by final screening decision: include, uncertain, all"
    ),
    prefill: bool = typer.Option(
        False, "--prefill", help="Copy auto_* suggestions into the form fields that declare it"
    ),
    pending_only: bool = typer.Option(
        True, "--pending-only/--recode", help="Skip items this coder already coded"
    ),
    double_sample: bool = typer.Option(
        False, "--double-sample", help="Only items of the double-coding sample (coding sample)"
    ),
) -> None:
    """Write the extraction sheet: one column per form field, plus auto_* suggestions.

    By default only items included in screening are exported; suggestions come from
    item concepts, `msrkit extract` detections and `msrkit enrich` signals.
    """
    from msrkit.coding import export_form, load_codings, suggestions
    from msrkit.screening import final_decisions, load_decisions
    from msrkit.storage import ItemStorage

    run_id = _resolve_run(_unwrap(run_id))
    coder = _unwrap(coder)
    status = _unwrap(status)
    if status not in ("include", "uncertain", "all"):
        console.print("[red]✗ --status must be include, uncertain or all.[/red]")
        raise typer.Exit(1)
    proto = _load_run_protocol(run_id, _unwrap(protocol))
    if proto is None or not proto.coding.fields:
        console.print("[red]✗ The protocol has no extraction form (set `coding.fields`).[/red]")
        raise typer.Exit(1)
    items = ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True)
    if status != "all":
        final = final_decisions(load_decisions(_cli.DATA_DIR, run_id))
        if not final:
            console.print(
                "[red]✗ No screening decisions for this run; import them first "
                "(`msrkit screen import`) or pass --status all.[/red]"
            )
            raise typer.Exit(1)
        items = [it for it in items if final.get(it.id) == status]
    if _unwrap(double_sample):
        sample_path = _cli.DATA_DIR / "coding" / run_id / "double_sample.json"
        if not sample_path.exists():
            console.print("[red]✗ No double-coding sample; run `msrkit coding sample`.[/red]")
            raise typer.Exit(1)
        chosen = set(json.loads(sample_path.read_text(encoding="utf-8"))["item_ids"])
        items = [it for it in items if it.id in chosen]
    if _unwrap(pending_only) and coder:
        done = {item_id for (item_id, c) in load_codings(_cli.DATA_DIR, run_id) if c == coder}
        items = [it for it in items if it.id not in done]
    systems = tuple(c for c in ("rag", "agente") if c in proto.concepts)
    auto = suggestions(items, _load_detections(run_id), systems)
    out = Path(
        _unwrap(output) or _cli.DATA_DIR / "coding" / run_id / f"form_{coder or 'blank'}.csv"
    )
    n = export_form(items, proto.coding, out, auto=auto, coder=coder, prefill=_unwrap(prefill))
    console.print(f"[green]✓ {n} items written to {out}[/green]")


@coding_app.command("import")
def coding_import(
    sheet: str = typer.Argument(..., help="Filled extraction sheet (.csv)"),
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    coder: str = typer.Option("", "--coder", help="Coder for rows with an empty coder column"),
) -> None:
    """Validate a filled extraction sheet and record it (nothing is imported on error)."""
    from msrkit.coding import append_codings, import_form
    from msrkit.screening import SheetError
    from msrkit.storage import ItemStorage

    run_id = _resolve_run(_unwrap(run_id))
    proto = _load_run_protocol(run_id, _unwrap(protocol))
    if proto is None or not proto.coding.fields:
        console.print("[red]✗ The protocol has no extraction form (set `coding.fields`).[/red]")
        raise typer.Exit(1)
    known = {it.id for it in ItemStorage(_cli.DATA_DIR).read_items(run_id)}
    try:
        records = import_form(
            Path(sheet), proto.coding, known, proto.gazetteer_data, default_coder=_unwrap(coder)
        )
    except SheetError as e:
        console.print(f"[red]✗ {e}[/red]")
        for problem in e.problems[:50]:
            console.print(f"  - {problem}")
        raise typer.Exit(1) from None
    path = append_codings(_cli.DATA_DIR, run_id, records)
    console.print(f"[green]✓ {len(records)} coded items recorded in {path}[/green]")


@coding_app.command("sample")
def coding_sample(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    rate: float | None = typer.Option(
        None, "--rate", help="Share of eligible items (default: coding.double_coding_rate)"
    ),
    seed: int = typer.Option(20260928, "--seed", help="Random seed (recorded)"),
) -> None:
    """Draw the double-coding sample (§15), stratified by source, among included items."""
    from msrkit.agreement import double_sample
    from msrkit.screening import final_decisions, load_decisions
    from msrkit.storage import ItemStorage

    run_id = _resolve_run(_unwrap(run_id))
    proto = _load_run_protocol(run_id, _unwrap(protocol))
    rate = _unwrap(rate) or (proto.coding.double_coding_rate if proto else 0.2)
    seed = _unwrap(seed)
    final = final_decisions(load_decisions(_cli.DATA_DIR, run_id))
    eligible = [
        it
        for it in ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True)
        if final.get(it.id) == "include"
    ]
    if not eligible:
        console.print("[red]✗ No included items yet (`msrkit screen import`).[/red]")
        raise typer.Exit(1)
    try:
        ids = double_sample(eligible, rate=rate, seed=seed)
    except ValueError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1) from None
    out = _cli.DATA_DIR / "coding" / run_id / "double_sample.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    record = {"rate": rate, "seed": seed, "eligible": len(eligible), "item_ids": ids}
    out.write_text(json.dumps(record, indent=2), encoding="utf-8")
    console.print(
        f"[green]✓ {len(ids)} of {len(eligible)} included items sampled for double "
        f"coding[/green] → {out}"
    )


@coding_app.command("status")
def coding_status(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
) -> None:
    """Coded items per coder and how many items have two or more coders."""
    from msrkit.coding import load_codings

    run_id = _resolve_run(_unwrap(run_id))
    latest = load_codings(_cli.DATA_DIR, run_id)
    per_coder: dict[str, int] = {}
    coders_by_item: dict[str, set[str]] = {}
    for item_id, coder in latest:
        per_coder[coder] = per_coder.get(coder, 0) + 1
        coders_by_item.setdefault(item_id, set()).add(coder)
    table = Table(title=f"Coding — {run_id}")
    table.add_column("Coder")
    table.add_column("Items")
    for coder, n in sorted(per_coder.items()):
        table.add_row(coder, str(n))
    console.print(table)
    double = sum(len(c) >= 2 for c in coders_by_item.values())
    console.print(f"Coded items: {len(coders_by_item)} · double-coded: {double}")


@app.command()
def agreement(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    all_fields: bool = typer.Option(
        False, "--all-fields", help="Every categorical field, not only coding.agreement"
    ),
) -> None:
    """Cohen's κ per dimension between coders (screening and extraction form, §15).

    Writes data/coding/<run_id>/agreement.json and disagreements.csv (one row per
    item and dimension, for the third coder).
    """
    import csv

    from msrkit.agreement import agreement as compute_agreement
    from msrkit.coding import load_codings
    from msrkit.screening import load_decisions

    run_id = _resolve_run(_unwrap(run_id))
    proto = _load_run_protocol(run_id, _unwrap(protocol))
    if proto is None:
        console.print("[red]✗ Protocol not found; pass --protocol.[/red]")
        raise typer.Exit(1)
    form = proto.coding
    dims = None if _unwrap(all_fields) or not form.agreement else form.agreement
    codings = load_codings(_cli.DATA_DIR, run_id)
    decisions = load_decisions(_cli.DATA_DIR, run_id)
    results = compute_agreement(form, codings, decisions, dimensions=dims)
    if not results:
        console.print("[yellow]No item was coded by two coders yet.[/yellow]")
        raise typer.Exit(0)

    table = Table(title=f"Inter-coder agreement — {run_id}")
    for col in ("Dimension", "Coders", "n", "Agreement", "κ", "Landis & Koch"):
        table.add_column(col)
    for r in results:
        table.add_row(
            r.dimension,
            " × ".join(r.coders),
            str(r.n),
            f"{r.percent:.0%}",
            "—" if r.kappa is None else f"{r.kappa:.2f}",
            r.interpretation,
        )
    console.print(table)

    out_dir = _cli.DATA_DIR / "coding" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "agreement.json").write_text(
        json.dumps([r.model_dump() for r in results], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    values: dict[tuple[str, str], dict[str, Any]] = {}
    for (item_id, coder), rec in codings.items():
        values[(item_id, coder)] = dict(rec.values)
    for (item_id, coder), d in decisions.items():
        values.setdefault((item_id, coder), {})["screening"] = d.decision
    with open(out_dir / "disagreements.csv", "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=";")
        writer.writerow(["item_id", "dimension", "coder_a", "value_a", "coder_b", "value_b"])
        for r in results:
            a, b = r.coders
            for item_id in r.disagreements:
                va = values.get((item_id, a), {}).get(r.dimension)
                vb = values.get((item_id, b), {}).get(r.dimension)
                writer.writerow([item_id, r.dimension, a, _cell(va), b, _cell(vb)])
    n_dis = sum(len(r.disagreements) for r in results)
    console.print(f"[green]✓ {n_dis} disagreement(s) written to {out_dir}[/green]")
