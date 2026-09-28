"""Analyses A1–A6 of Protocol E2 v2 (§14) over units of analysis.

A *unit* is what is counted once: a GitHub repository (all its items together)
or a single item elsewhere. Units come from one of two bases:

- `detections` (automatic, `msrkit extract`): tools with their best evidence
  level and methods; system label from the item concepts;
- `coding` (manual, `msrkit coding`): the extraction form. With several coders
  the consensus is used — an `enum` value only when all coders agree, the
  intersection for `multi` fields — so disputed values are left out until a
  third coder resolves them.

Reported numbers always say which basis they come from.
"""

from __future__ import annotations

import csv
import math
from collections import Counter
from itertools import combinations
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from msrkit.enrich import repo_of
from msrkit.extract import LEVEL_RANK

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from msrkit.coding import CodingRecord
    from msrkit.extract import Detection
    from msrkit.models import Item

Basis = Literal["detections", "coding"]
SYSTEMS = ("rag", "agente")

# Form fields (Annex A) read by the coding basis.
FIELD_SYSTEM = "sistema"
FIELD_LEVEL = "nivel_evidencia"
FIELD_TOOLS = "ferramentas"
FIELD_METHODS = "metodos"
FIELD_FAILURES = "modos_falha"
FIELD_ORACLE = "tipo_oraculo"
FIELD_AGGREGATION = "agregacao"


class Unit(BaseModel):
    """One unit of analysis."""

    id: str  # owner/repo, or item id
    source: str
    item_ids: list[str] = []
    systems: list[str] = []  # subset of SYSTEMS
    tools: dict[str, str] = {}  # tool id -> best evidence level
    methods: list[str] = []
    failure_modes: list[str] = []
    oracle: str | None = None
    aggregation: str | None = None


def _unit_key(item: Item) -> str:
    return repo_of(item) or item.id


def units_from_detections(items: list[Item], detections: list[Detection]) -> list[Unit]:
    units: dict[str, Unit] = {}
    item_unit: dict[str, str] = {}
    for it in items:
        key = _unit_key(it)
        unit = units.setdefault(key, Unit(id=key, source=it.source))
        unit.item_ids.append(it.id)
        unit.systems = sorted(set(unit.systems) | {c for c in it.concepts if c in SYSTEMS})
        item_unit[it.id] = key
    for d in detections:
        unit_id = item_unit.get(d.item_id) or d.repo
        if unit_id is None or unit_id not in units:
            continue
        unit = units[unit_id]
        if d.entry_type == "tool":
            current = unit.tools.get(d.entry_id)
            if current is None or LEVEL_RANK[d.level] > LEVEL_RANK[current]:
                unit.tools[d.entry_id] = d.level
        elif d.entry_id not in unit.methods:
            unit.methods.append(d.entry_id)
    for unit in units.values():
        unit.methods.sort()
    return sorted(units.values(), key=lambda u: u.id)


def consensus(records: list[CodingRecord]) -> dict[str, Any]:
    """Values all coders agree on: unanimous enums/bools, intersection of lists."""
    if not records:
        return {}
    out: dict[str, Any] = {}
    for name in records[0].values:
        values = [r.values.get(name) for r in records]
        lists = [v for v in values if isinstance(v, list)]
        if len(lists) == len(values):
            common = set(lists[0]).intersection(*lists[1:])
            out[name] = [v for v in lists[0] if v in common]
        elif all(v == values[0] for v in values):
            out[name] = values[0]
        else:
            out[name] = None
    return out


def units_from_codings(
    items: list[Item], codings: dict[tuple[str, str], CodingRecord]
) -> list[Unit]:
    """One unit per coded item, merged per repository (union of coded values)."""
    by_item: dict[str, list[CodingRecord]] = {}
    for (item_id, _coder), rec in sorted(codings.items()):
        by_item.setdefault(item_id, []).append(rec)
    item_map = {it.id: it for it in items}
    units: dict[str, Unit] = {}
    for item_id, recs in by_item.items():
        item = item_map.get(item_id)
        if item is None:
            continue
        values = consensus(recs)
        key = _unit_key(item)
        unit = units.setdefault(key, Unit(id=key, source=item.source))
        unit.item_ids.append(item_id)
        unit.systems = sorted(set(unit.systems) | set(values.get(FIELD_SYSTEM) or []))
        level = values.get(FIELD_LEVEL)
        for tool in values.get(FIELD_TOOLS) or []:
            current = unit.tools.get(tool)
            if level and (current is None or LEVEL_RANK[level] > LEVEL_RANK[current]):
                unit.tools[tool] = level
            elif current is None:
                unit.tools[tool] = "N1"
        unit.methods = sorted(set(unit.methods) | set(values.get(FIELD_METHODS) or []))
        unit.failure_modes = sorted(set(unit.failure_modes) | set(values.get(FIELD_FAILURES) or []))
        unit.oracle = unit.oracle or values.get(FIELD_ORACLE)
        unit.aggregation = unit.aggregation or values.get(FIELD_AGGREGATION)
    return sorted(units.values(), key=lambda u: u.id)


# -- A1/A2: frequency and co-occurrence ---------------------------------------


class FrequencyRow(BaseModel):
    entry: str
    kind: Literal["tool", "method"]
    units: int
    n1: int = 0  # tools: units at each best level (disjoint)
    n2: int = 0
    n3: int = 0
    rag: int = 0  # units labelled rag (a hybrid unit counts in both)
    agente: int = 0
    sources: dict[str, int] = {}


def frequency(units: list[Unit]) -> list[FrequencyRow]:
    rows: dict[tuple[str, str], FrequencyRow] = {}
    for u in units:
        entries: list[tuple[str, Literal["tool", "method"], str | None]] = [
            (t, "tool", lv) for t, lv in u.tools.items()
        ]
        entries += [(m, "method", None) for m in u.methods]
        for entry, kind, level in entries:
            row = rows.setdefault(
                (kind, entry),
                FrequencyRow(entry=entry, kind=kind, units=0),
            )
            row.units += 1
            if level:
                setattr(row, level.lower(), getattr(row, level.lower()) + 1)
            row.rag += "rag" in u.systems
            row.agente += "agente" in u.systems
            row.sources[u.source] = row.sources.get(u.source, 0) + 1
    return sorted(rows.values(), key=lambda r: (-r.units, r.kind != "tool", r.entry))


class PairRow(BaseModel):
    a: str
    b: str
    together: int  # units with both
    a_units: int
    b_units: int
    jaccard: float
    lift: float  # P(a,b) / (P(a) P(b)); > 1 means they co-occur more than by chance


def cooccurrence(
    units: list[Unit], pairs: str = "tool-method", min_together: int = 1
) -> list[PairRow]:
    """Co-occurrence within units: `tool-method`, `tool-tool` or `method-method`."""
    n = len(units)
    count: Counter[str] = Counter()
    together: Counter[tuple[str, str]] = Counter()
    for u in units:
        tools, methods = sorted(u.tools), list(u.methods)
        for e in {*tools, *methods}:
            count[e] += 1
        if pairs == "tool-method":
            combos = [(t, m) for t in tools for m in methods]
        elif pairs == "tool-tool":
            combos = list(combinations(tools, 2))
        elif pairs == "method-method":
            combos = list(combinations(sorted(methods), 2))
        else:
            raise ValueError("pairs must be tool-method, tool-tool or method-method")
        together.update(combos)
    rows = []
    for (a, b), both in together.items():
        if both < min_together:
            continue
        union = count[a] + count[b] - both
        rows.append(
            PairRow(
                a=a,
                b=b,
                together=both,
                a_units=count[a],
                b_units=count[b],
                jaccard=round(both / union, 4) if union else 0.0,
                lift=round(both * n / (count[a] * count[b]), 4) if n else 0.0,
            )
        )
    return sorted(rows, key=lambda r: (-r.together, -r.lift, r.a, r.b))


# -- statistics ---------------------------------------------------------------


def binomial_greater(successes: int, n: int, p0: float = 0.5) -> float:
    """One-sided exact binomial p-value for H1: p > p0."""
    return sum(math.comb(n, k) * p0**k * (1 - p0) ** (n - k) for k in range(successes, n + 1))


# -- output -------------------------------------------------------------------


def write_csv(rows: Sequence[BaseModel], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [r.model_dump() for r in rows]
    fields = list(data[0]) if data else []
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter=";")
        if fields:
            writer.writeheader()
        for row in data:
            writer.writerow(
                {
                    k: "; ".join(f"{a}={b}" for a, b in v.items()) if isinstance(v, dict) else v
                    for k, v in row.items()
                }
            )
    return path
