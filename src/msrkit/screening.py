"""Screening sheets: export items for manual screening and import the decisions.

Protocol E2 v2 §8 (eligibility) and §11 (assisted screening). The criteria come
from the protocol (`screening.inclusion` / `screening.exclusion`), so the same
workflow serves any study. Sheets are CSV (`;`, UTF-8 with BOM) so Excel in
pt-BR opens them directly.

A sheet row has one column per criterion (mark with x/s/sim/y/yes/1), a
`decision` (include | exclude | uncertain), a `reason` and the `coder`.
Imported decisions are appended to data/screening/<run_id>/decisions.jsonl,
one record per (item, coder), keeping every coder's answer for the agreement
check (step T5). The latest import of a coder for an item wins.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from pathlib import Path

    from msrkit.models import Item

Decision = Literal["include", "exclude", "uncertain"]
DECISIONS = ("include", "exclude", "uncertain")
TRUE_MARKS = {"x", "s", "sim", "y", "yes", "1", "true", "✓"}
DELIMITER = ";"


class ScreeningCriteria(BaseModel):
    """Eligibility criteria as declared in the protocol."""

    inclusion: dict[str, str] = {}
    exclusion: dict[str, str] = {}
    batch_size: int = 25


class ScreeningDecision(BaseModel):
    """One coder's decision on one item."""

    item_id: str
    coder: str
    decision: Decision
    criteria_met: list[str] = []  # criterion ids marked in the sheet
    reason: str = ""
    notes: str = ""
    decided_at: str
    sheet_sha256: str


class SheetError(ValueError):
    """The sheet has rows that cannot be imported; `problems` lists them."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} problem(s) in the screening sheet")


BASE_COLUMNS = [
    "item_id", "batch", "source", "kind", "url", "title", "concepts", "matched_terms",
    "has_ci", "contributors",
]  # fmt: skip
TAIL_COLUMNS = ["decision", "reason", "coder", "notes"]


def sheet_columns(criteria: ScreeningCriteria, extra: list[str] | None = None) -> list[str]:
    return [*BASE_COLUMNS, *(extra or []), *criteria.inclusion, *criteria.exclusion, *TAIL_COLUMNS]


def export_sheet(
    items: list[Item],
    criteria: ScreeningCriteria,
    path: Path,
    coder: str = "",
    extra_columns: dict[str, dict[str, str]] | None = None,
) -> int:
    """Write the screening sheet; returns the number of rows.

    Items are written in the given order (see step T3 for relevance ordering)
    and grouped in batches of `criteria.batch_size`.
    """
    extra_columns = extra_columns or {}
    extra_names = sorted({k for cols in extra_columns.values() for k in cols})
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=sheet_columns(criteria, extra_names), delimiter=DELIMITER
        )
        writer.writeheader()
        for idx, item in enumerate(items):
            row = {
                "item_id": item.id,
                "batch": idx // max(criteria.batch_size, 1) + 1,
                "source": item.source,
                "kind": item.kind.value,
                "url": str(item.url),
                "title": item.title or "",
                "concepts": ", ".join(item.concepts),
                "matched_terms": ", ".join(sorted({h.term for h in item.matched_terms})),
                "has_ci": "" if item.tech.has_ci is None else str(item.tech.has_ci).lower(),
                "contributors": "" if item.tech.contributors is None else item.tech.contributors,
                "coder": coder,
                **extra_columns.get(item.id, {}),
            }
            writer.writerow(row)
    return len(items)


def _marked(value: str | None) -> bool:
    return (value or "").strip().lower() in TRUE_MARKS


def import_sheet(
    path: Path,
    criteria: ScreeningCriteria,
    known_ids: set[str],
    default_coder: str = "",
) -> list[ScreeningDecision]:
    """Parse and validate a filled sheet. Rows without a decision are skipped.

    Raises SheetError listing every invalid row, so nothing is half-imported.
    """
    raw = path.read_bytes()
    sheet_hash = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8-sig")
    dialect_delim = DELIMITER if text.split("\n", 1)[0].count(DELIMITER) else ","
    reader = csv.DictReader(text.splitlines(), delimiter=dialect_delim)

    now = datetime.now(UTC).isoformat()
    decisions: list[ScreeningDecision] = []
    problems: list[str] = []
    for line_no, row in enumerate(reader, start=2):
        decision = (row.get("decision") or "").strip().lower()
        if not decision:
            continue
        item_id = (row.get("item_id") or "").strip()
        coder = (row.get("coder") or default_coder).strip()
        where = f"line {line_no} ({item_id or 'no item_id'})"
        if item_id not in known_ids:
            problems.append(f"{where}: unknown item_id")
            continue
        if decision not in DECISIONS:
            problems.append(f"{where}: decision must be one of {', '.join(DECISIONS)}")
            continue
        if not coder:
            problems.append(f"{where}: coder is empty (fill the column or pass --coder)")
            continue
        met = [c for c in [*criteria.inclusion, *criteria.exclusion] if _marked(row.get(c))]
        excl = [c for c in met if c in criteria.exclusion]
        reason = (row.get("reason") or "").strip()
        if decision == "include" and excl:
            problems.append(f"{where}: included but exclusion criteria marked: {', '.join(excl)}")
            continue
        if decision == "exclude" and not excl and not reason:
            problems.append(f"{where}: excluded without an exclusion criterion or a reason")
            continue
        decisions.append(
            ScreeningDecision(
                item_id=item_id,
                coder=coder,
                decision=decision,  # type: ignore[arg-type]
                criteria_met=met,
                reason=reason,
                notes=(row.get("notes") or "").strip(),
                decided_at=now,
                sheet_sha256=sheet_hash,
            )
        )
    if problems:
        raise SheetError(problems)
    return decisions


def decisions_path(data_dir: Path, run_id: str) -> Path:
    return data_dir / "screening" / run_id / "decisions.jsonl"


def append_decisions(data_dir: Path, run_id: str, decisions: list[ScreeningDecision]) -> Path:
    path = decisions_path(data_dir, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for d in decisions:
            fh.write(d.model_dump_json() + "\n")
    return path


def load_decisions(data_dir: Path, run_id: str) -> dict[tuple[str, str], ScreeningDecision]:
    """Latest decision per (item_id, coder)."""
    path = decisions_path(data_dir, run_id)
    latest: dict[tuple[str, str], ScreeningDecision] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = ScreeningDecision.model_validate(json.loads(line))
                latest[(d.item_id, d.coder)] = d
    return latest


def final_decisions(latest: dict[tuple[str, str], ScreeningDecision]) -> dict[str, Decision]:
    """One decision per item: agreed decision, or `uncertain` when coders disagree."""
    by_item: dict[str, set[str]] = {}
    for (item_id, _coder), d in latest.items():
        by_item.setdefault(item_id, set()).add(d.decision)
    return {
        item_id: next(iter(ds)) if len(ds) == 1 else "uncertain"  # type: ignore[misc]
        for item_id, ds in by_item.items()
    }
