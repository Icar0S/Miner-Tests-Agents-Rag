"""Analysis commands (Protocol E2 v2, §14): A1–A6 over units of analysis."""

from __future__ import annotations

from typing import Any

import typer
from rich.table import Table

from msrkit import cli as _cli
from msrkit.cli._common import (
    _load_detections,
    _load_run_protocol,
    _resolve_run,
    _unwrap,
    app,
    console,
)

analyze_app = typer.Typer(
    help="Analyses A1–A6 (§14): frequency, coverage, oracles, topics, compare."
)
app.add_typer(analyze_app, name="analyze")

BASIS_HELP = "detections (automatic, msrkit extract) or coding (manual form, consensus)"


def _load_units(run_id: str, basis: str) -> list[Any]:
    from msrkit.analysis import units_from_codings, units_from_detections
    from msrkit.coding import load_codings
    from msrkit.storage import ItemStorage

    items = ItemStorage(_cli.DATA_DIR).read_items(run_id, prefer_deduped=True)
    if basis == "detections":
        detections = _load_detections(run_id)
        if not detections:
            console.print("[red]✗ No detections; run `msrkit extract` first.[/red]")
            raise typer.Exit(1)
        return units_from_detections(items, detections)
    if basis == "coding":
        codings = load_codings(_cli.DATA_DIR, run_id)
        if not codings:
            console.print("[red]✗ No codings; import them with `msrkit coding import`.[/red]")
            raise typer.Exit(1)
        return units_from_codings(items, codings)
    console.print("[red]✗ --basis must be detections or coding.[/red]")
    raise typer.Exit(1)


def _out_dir(run_id: str) -> Any:
    return _cli.DATA_DIR / "reports" / run_id / "analysis"


@analyze_app.command("frequency")
def analyze_frequency(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    basis: str = typer.Option("detections", "--basis", help=BASIS_HELP),
    pairs: str = typer.Option(
        "tool-method", "--pairs", help="Co-occurrence: tool-method, tool-tool, method-method"
    ),
    top: int = typer.Option(20, "--top", help="Rows shown"),
) -> None:
    """A1/A2: units per tool and method (by evidence level and system) and co-occurrence."""
    from msrkit.analysis import cooccurrence, frequency, write_csv

    run_id = _resolve_run(_unwrap(run_id))
    basis = _unwrap(basis)
    top = _unwrap(top)
    units = _load_units(run_id, basis)
    try:
        pair_rows = cooccurrence(units, _unwrap(pairs))
    except ValueError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1) from None
    rows = frequency(units)
    out = _out_dir(run_id)
    write_csv(rows, out / f"frequency_{basis}.csv")
    write_csv(pair_rows, out / f"cooccurrence_{_unwrap(pairs)}_{basis}.csv")

    table = Table(title=f"Frequency ({basis}) — {len(units)} units")
    for col in ("Entry", "Kind", "Units", "N1", "N2", "N3", "rag", "agente"):
        table.add_column(col)
    for r in rows[:top]:
        levels = (str(r.n1), str(r.n2), str(r.n3)) if r.kind == "tool" else ("—", "—", "—")
        table.add_row(r.entry, r.kind, str(r.units), *levels, str(r.rag), str(r.agente))
    console.print(table)
    pt = Table(title=f"Co-occurrence {_unwrap(pairs)}")
    for col in ("A", "B", "Together", "Jaccard", "Lift"):
        pt.add_column(col)
    for p in pair_rows[:top]:
        pt.add_row(p.a, p.b, str(p.together), f"{p.jaccard:.2f}", f"{p.lift:.2f}")
    console.print(pt)
    console.print(f"[green]✓ CSV written to {out}[/green]")


@analyze_app.command("coverage")
def analyze_coverage(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
) -> None:
    """A3: failure mode × tool/method coverage, for RAG and agents (coding basis).

    Writes the long table, one wide matrix per system and a per-mode summary that
    lists catalog modes nobody addresses.
    """
    from msrkit.analysis import coverage, coverage_matrix, write_csv, write_matrix

    run_id = _resolve_run(_unwrap(run_id))
    units = _load_units(run_id, "coding")
    proto = _load_run_protocol(run_id, _unwrap(protocol))
    catalog = proto.coding.failure_modes if proto is not None else {}
    cells, summaries = coverage(units, catalog)
    out = _out_dir(run_id)
    write_csv(cells, out / "coverage_long.csv")
    write_csv(summaries, out / "coverage_summary.csv")
    modes = [s.failure_mode for s in summaries]
    for system in ("rag", "agente", None):
        columns, rows = coverage_matrix(cells, modes, system)
        write_matrix(columns, rows, modes, out / f"coverage_matrix_{system or 'all'}.csv")

    table = Table(title=f"Failure-mode coverage (coding) — {len(units)} units")
    for col in ("Mode", "Label", "Units", "rag", "agente", "Tools", "Methods", "Top"):
        table.add_column(col)
    for s in summaries:
        style = "" if s.covered else "dim"
        table.add_row(
            s.failure_mode, s.label, str(s.units), str(s.rag), str(s.agente),
            str(s.tools), str(s.methods), s.top_entries, style=style,
        )  # fmt: skip
    console.print(table)
    uncovered = [s.failure_mode for s in summaries if not s.covered]
    if uncovered:
        console.print(f"Not addressed by any unit: {', '.join(uncovered)}")
    console.print(f"[green]✓ CSV written to {out}[/green]")


@analyze_app.command("oracles")
def analyze_oracles(
    run_id: str | None = typer.Option(None, "--run", help="Run ID (defaults to latest)"),
    basis: str = typer.Option("coding", "--basis", help=BASIS_HELP),
    protocol: str | None = typer.Option(None, "--protocol", "-p", help="Protocol file"),
    threshold: float = typer.Option(
        0.5, "--threshold", help="H2: share of atomic oracles the majority must exceed"
    ),
) -> None:
    """A4: distribution on the oracle ladder and by aggregation; tests H2.

    On the detections basis each method contributes its typical rung from the
    gazetteer; aggregation (and so H2) needs the coding basis.
    """
    from msrkit.analysis import aggregation_test, oracle_distribution, write_csv

    run_id = _resolve_run(_unwrap(run_id))
    basis = _unwrap(basis)
    threshold = _unwrap(threshold)
    units = _load_units(run_id, basis)
    rungs: dict[str, str] = {}
    proto = _load_run_protocol(run_id, _unwrap(protocol))
    if proto is not None and proto.gazetteer_data is not None:
        rungs = {m.id: m.oracle for m in proto.gazetteer_data.methods if m.oracle}
    rows = oracle_distribution(units, rungs if basis == "detections" else None)
    out = _out_dir(run_id)
    write_csv(rows, out / f"oracles_{basis}.csv")

    table = Table(title=f"Oracle ladder ({basis}) — {len(units)} units")
    for col in ("Rung", "all", "rag", "agente"):
        table.add_column(col)
    by = {(r.system, r.rung): r for r in rows}
    for rung in dict.fromkeys(r.rung for r in rows):
        table.add_row(
            rung,
            *(
                f"{by[(s, rung)].units} ({by[(s, rung)].share:.0%})"
                for s in ("all", "rag", "agente")
            ),
        )
    console.print(table)

    if basis != "coding":
        console.print("[dim]Aggregation (H2) needs --basis coding.[/dim]")
        return
    tests = [aggregation_test(units, s, threshold) for s in ("all", "rag", "agente")]
    write_csv(tests, out / "h2_aggregation.csv")
    h2 = Table(title=f"H2 — atomic oracles > {threshold:.0%}")
    for col in ("System", "Atomic", "Aggregated", "Unidentified", "Atomic %", "95% CI", "p", "H2"):
        h2.add_column(col)
    for t in tests:
        h2.add_row(
            t.system, str(t.atomic), str(t.aggregated), str(t.unidentified),
            f"{t.proportion_atomic:.0%}", f"{t.ci95[0]:.0%}–{t.ci95[1]:.0%}",
            f"{t.p_value:.4f}", "supported" if t.supported else "not supported",
        )  # fmt: skip
    console.print(h2)
    console.print(f"[green]✓ CSV written to {out}[/green]")
