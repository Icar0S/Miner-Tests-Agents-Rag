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

Ordering (step T3): items are ranked by a transparent relevance score —
distinct matched terms, concept coverage (system and testing), the best
evidence level detected and repository practice signals — and cut into fixed
batches on the *whole* run, so an item keeps its batch number across
re-exports and coders. Ranking only orders the work; every item is screened.
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

    from msrkit.extract import Detection
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
    "item_id", "batch", "rank", "score", "source", "kind", "url", "title", "concepts",
    "matched_terms", "evidence", "has_ci", "contributors",
]  # fmt: skip

# Relevance weights (ADR-027). Kept small and additive so the score is explainable.
W_TERM = 1.0  # per distinct matched term, capped
MAX_TERMS = 5
W_SYSTEM = 2.0  # at least one system concept (e.g. rag, agente)
W_TESTING = 2.0  # the testing concept
W_LEVEL = {"N1": 1.0, "N2": 3.0, "N3": 5.0}
W_PRACTICE = 1.0  # each of has_tests / has_ci
TESTING_CONCEPT = "teste"
SYSTEM_CONCEPTS = ("rag", "agente")


class Ranked(BaseModel):
    """An item's place in the screening order."""

    item_id: str
    rank: int  # 1 = most relevant
    batch: int
    score: float
    evidence: str = ""  # best evidence level detected, if any


def relevance_score(item: Item, detections: list[Detection] | None = None) -> tuple[float, str]:
    """Score and best evidence level of one item (higher = screen earlier)."""
    terms = {h.term.lower() for h in item.matched_terms}
    score = W_TERM * min(len(terms), MAX_TERMS)
    if any(c in item.concepts for c in SYSTEM_CONCEPTS):
        score += W_SYSTEM
    if TESTING_CONCEPT in item.concepts:
        score += W_TESTING
    levels = [d.level for d in detections or []]
    best = max(levels, key=lambda lv: W_LEVEL[lv]) if levels else ""
    if best:
        score += W_LEVEL[best]
    score += W_PRACTICE * (bool(item.tech.has_tests) + bool(item.tech.has_ci))
    return round(score, 2), best


def rank_items(
    items: list[Item], detections: list[Detection] | None = None, batch_size: int = 25
) -> dict[str, Ranked]:
    """Rank the whole run and assign fixed batches.

    Ties are broken by item id, so the order is deterministic for a given run.
    """
    by_item: dict[str, list[Detection]] = {}
    for d in detections or []:
        by_item.setdefault(d.item_id, []).append(d)
    scored = [(it.id, *relevance_score(it, by_item.get(it.id))) for it in items]
    scored.sort(key=lambda row: (-row[1], row[0]))
    size = max(batch_size, 1)
    return {
        item_id: Ranked(
            item_id=item_id, rank=pos + 1, batch=pos // size + 1, score=score, evidence=best
        )
        for pos, (item_id, score, best) in enumerate(scored)
    }


TAIL_COLUMNS = ["decision", "reason", "coder", "notes"]


def sheet_columns(criteria: ScreeningCriteria, extra: list[str] | None = None) -> list[str]:
    return [*BASE_COLUMNS, *(extra or []), *criteria.inclusion, *criteria.exclusion, *TAIL_COLUMNS]


def export_sheet(
    items: list[Item],
    criteria: ScreeningCriteria,
    path: Path,
    coder: str = "",
    extra_columns: dict[str, dict[str, str]] | None = None,
    ranking: dict[str, Ranked] | None = None,
) -> int:
    """Write the screening sheet; returns the number of rows.

    With `ranking` (see rank_items), rows follow the ranking and carry its fixed
    batch numbers; without it, items keep the given order in batches of
    `criteria.batch_size`.
    """
    extra_columns = extra_columns or {}
    if ranking is not None:
        items = sorted(items, key=lambda it: ranking[it.id].rank)
    extra_names = sorted({k for cols in extra_columns.values() for k in cols})
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=sheet_columns(criteria, extra_names), delimiter=DELIMITER
        )
        writer.writeheader()
        for idx, item in enumerate(items):
            ranked = ranking.get(item.id) if ranking else None
            row = {
                "item_id": item.id,
                "batch": ranked.batch if ranked else idx // max(criteria.batch_size, 1) + 1,
                "rank": ranked.rank if ranked else idx + 1,
                "score": ranked.score if ranked else "",
                "evidence": ranked.evidence if ranked else "",
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
    by_item: dict[str, set[Decision]] = {}
    for (item_id, _coder), d in latest.items():
        by_item.setdefault(item_id, set()).add(d.decision)
    return {
        item_id: next(iter(ds)) if len(ds) == 1 else "uncertain" for item_id, ds in by_item.items()
    }
