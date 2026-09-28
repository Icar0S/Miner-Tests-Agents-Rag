"""Instrument validation commands: recall and precision (§12.3)."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.table import Table

from msrkit import cli as _cli
from msrkit.cli._common import (
    _load_detections,
    _resolve_run,
    _unwrap,
    app,
    console,
)

precision_app = typer.Typer(help="Extraction precision on a stratified sample (§12.3).")


app.add_typer(precision_app, name="precision")


@precision_app.command("sample")
def precision_sample_cmd(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    n: int = typer.Option(200, "--n", help="Sample size"),
    seed: int = typer.Option(20260928, "--seed", help="Random seed (recorded)"),
) -> None:
    """Export detections stratified by source × evidence level for manual judgement.

    Fill `correct` with yes/no, then run `msrkit precision score <sheet>`.
    """
    from msrkit.storage import ItemStorage
    from msrkit.validation import detection_key, export_precision_sheet, precision_sample

    run_id = _resolve_run(_unwrap(run_id))
    detections = _load_detections(run_id)
    if not detections:
        console.print("[red]✗ No detections for this run; run `msrkit extract` first.[/red]")
        raise typer.Exit(1)
    sample, sizes = precision_sample(detections, n=_unwrap(n), seed=_unwrap(seed))
    items = {it.id: it for it in ItemStorage(_cli.DATA_DIR).read_items(run_id)}
    out_dir = _cli.DATA_DIR / "validation" / run_id
    sheet = out_dir / "precision_sample.csv"
    export_precision_sheet(sample, items, sheet)
    record = {
        "n": len(sample),
        "seed": _unwrap(seed),
        "population": len(detections),
        "strata": sizes,
        "keys": [detection_key(d) for d in sample],
    }
    (out_dir / "precision_sample.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    console.print(
        f"[green]✓ {len(sample)} of {len(detections)} detections in {len(sizes)} strata "
        f"written to {sheet}[/green]"
    )


@precision_app.command("score")
def precision_score_cmd(
    sheet: str = typer.Argument(..., help="Judged precision sheet (.csv)"),
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
) -> None:
    """Precision per stratum and overall (weighted by stratum population, Wilson 95% CI)."""
    from msrkit.screening import SheetError
    from msrkit.validation import score_precision

    run_id = _resolve_run(_unwrap(run_id))
    record_path = _cli.DATA_DIR / "validation" / run_id / "precision_sample.json"
    sizes = None
    if record_path.exists():
        sizes = json.loads(record_path.read_text(encoding="utf-8")).get("strata")
    try:
        report = score_precision(Path(sheet), sizes)
    except SheetError as e:
        console.print(f"[red]✗ {e}[/red]")
        for problem in e.problems[:50]:
            console.print(f"  - {problem}")
        raise typer.Exit(1) from None
    if not report.judged:
        console.print("[yellow]No judged rows (fill the `correct` column).[/yellow]")
        raise typer.Exit(0)
    table = Table(title=f"Extraction precision — {run_id}")
    for col in ("Stratum", "Population", "Judged", "Correct", "Precision"):
        table.add_column(col)
    for r in report.strata:
        table.add_row(
            r.stratum, str(r.population), str(r.judged), str(r.correct), f"{r.precision:.0%}"
        )
    console.print(table)
    lo, hi = report.ci95
    console.print(
        f"Weighted precision: [bold]{report.weighted_precision:.1%}[/bold] "
        f"(sample {report.correct}/{report.judged}, Wilson 95% CI {lo:.1%}–{hi:.1%})"
    )
    for level, value in report.by_level.items():
        console.print(f"  {level}: {value:.1%}")
    if sizes is None:
        console.print("[dim]No sample record: strata weighted by the sheet's own counts.[/dim]")
    out = _cli.DATA_DIR / "validation" / run_id / "precision_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.model_dump_json(indent=2), encoding="utf-8")


@app.command()
def recall(
    gold: str = typer.Argument(..., help="Gold set YAML (see msrkit.validation)"),
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    all_runs: bool = typer.Option(False, "--all", "-a", help="Use items from every run"),
) -> None:
    """Recall of the miner on a gold set curated before collection (§10.3, §12.3)."""
    from msrkit.extract import Detection
    from msrkit.storage import ItemStorage
    from msrkit.validation import load_gold, measure_recall

    storage = ItemStorage(_cli.DATA_DIR)
    runs = storage.list_runs() if _unwrap(all_runs) else [_resolve_run(_unwrap(run_id))]
    items = [it for r in runs for it in storage.read_items(r)]
    detections: list[Detection] = []
    for r in runs:
        path = _cli.DATA_DIR / "extract" / r / "detections.jsonl"
        if path.exists():
            detections.extend(
                Detection.model_validate_json(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
    try:
        report = measure_recall(load_gold(gold), items, detections)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1) from None

    table = Table(title=f"Gold set recall — {', '.join(runs)}")
    for col in ("Host", "Retrieved", "Total"):
        table.add_column(col)
    for host, (got, total) in report.by_host.items():
        table.add_row(host, str(got), str(total))
    console.print(table)
    console.print(
        f"[bold]Collection recall:[/bold] {report.retrieved}/{report.total} ({report.recall:.1%})"
    )
    if report.tools_expected:
        console.print(
            f"[bold]Detection recall:[/bold] {report.tools_detected}/{report.tools_expected} "
            f"({report.detection_recall:.1%})"
        )
    for url in report.missed:
        console.print(f"  [yellow]missed[/yellow] {url}")
    for miss in report.tools_missed:
        console.print(f"  [yellow]tool not detected[/yellow] {miss}")
