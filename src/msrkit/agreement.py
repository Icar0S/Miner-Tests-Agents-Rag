"""Inter-coder agreement (Protocol E2 v2, §15).

- `double_sample`: the reproducible sample for independent double coding
  (≥20% of eligible items), stratified by source.
- `agreement`: Cohen's κ per dimension for every pair of coders, over the
  items both coded. Dimensions are the screening decision and the categorical
  form fields (`enum`, `bool`, `multi`).

For `multi` fields each (item, value) pair is a binary decision — was the value
coded or not — over the values either coder used, and κ is computed on the
pooled decisions; per-value κ is also reported. An empty `enum`/`bool` is its
own category ("∅"), since leaving a dimension blank is itself a decision.
Free-text fields have no κ.
"""

from __future__ import annotations

import random
from collections import Counter
from itertools import combinations
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from msrkit.coding import CodingForm, CodingRecord
    from msrkit.models import Item
    from msrkit.screening import ScreeningDecision

EMPTY = "∅"
DEFAULT_SEED = 20260928


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    """Cohen's κ for two coders' labels on the same units (None when undefined)."""
    if len(a) != len(b):
        raise ValueError("both coders must label the same units")
    n = len(a)
    if n == 0:
        return None
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in ca.keys() | cb.keys()) / (n * n)
    if expected == 1.0:  # both coders used a single, identical category
        return None
    return (observed - expected) / (1 - expected)


def interpret(kappa: float | None) -> str:
    """Landis & Koch (1977) bands."""
    if kappa is None:
        return "undefined"
    for bound, label in (
        (0.0, "poor"),
        (0.20, "slight"),
        (0.40, "fair"),
        (0.60, "moderate"),
        (0.80, "substantial"),
    ):
        if kappa <= bound:
            return label
    return "almost perfect"


class PairAgreement(BaseModel):
    """Agreement between two coders on one dimension."""

    dimension: str
    coders: tuple[str, str]
    n: int  # items coded by both
    percent: float  # observed agreement on items (all values equal)
    kappa: float | None
    interpretation: str
    per_value: dict[str, float | None] = {}  # multi fields only
    disagreements: list[str] = []  # item ids


def _label(value: Any) -> str:
    if value is None or value == []:
        return EMPTY
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _pair(
    dimension: str,
    coders: tuple[str, str],
    values_a: dict[str, Any],
    values_b: dict[str, Any],
    multi: bool,
) -> PairAgreement | None:
    shared = sorted(values_a.keys() & values_b.keys())
    if not shared:
        return None
    if multi:
        sets_a = {i: set(values_a[i] or []) for i in shared}
        sets_b = {i: set(values_b[i] or []) for i in shared}
        used = sorted(set().union(*sets_a.values(), *sets_b.values()))
        pooled_a: list[str] = []
        pooled_b: list[str] = []
        per_value: dict[str, float | None] = {}
        for v in used:
            col_a = ["1" if v in sets_a[i] else "0" for i in shared]
            col_b = ["1" if v in sets_b[i] else "0" for i in shared]
            per_value[v] = cohen_kappa(col_a, col_b)
            pooled_a += col_a
            pooled_b += col_b
        kappa = cohen_kappa(pooled_a, pooled_b) if used else None
        disagree = [i for i in shared if sets_a[i] != sets_b[i]]
    else:
        labels_a = [_label(values_a[i]) for i in shared]
        labels_b = [_label(values_b[i]) for i in shared]
        kappa = cohen_kappa(labels_a, labels_b)
        per_value = {}
        disagree = [i for i, x, y in zip(shared, labels_a, labels_b, strict=True) if x != y]
    return PairAgreement(
        dimension=dimension,
        coders=coders,
        n=len(shared),
        percent=round(1 - len(disagree) / len(shared), 4),
        kappa=None if kappa is None else round(kappa, 4),
        interpretation=interpret(kappa),
        per_value={k: None if v is None else round(v, 4) for k, v in per_value.items()},
        disagreements=disagree,
    )


def agreement(
    form: CodingForm,
    codings: dict[tuple[str, str], CodingRecord],
    decisions: dict[tuple[str, str], ScreeningDecision] | None = None,
    dimensions: list[str] | None = None,
) -> list[PairAgreement]:
    """κ per dimension and coder pair. `dimensions` restricts the form fields."""
    results: list[PairAgreement] = []

    if decisions:
        by_coder: dict[str, dict[str, Any]] = {}
        for (item_id, coder), d in decisions.items():
            by_coder.setdefault(coder, {})[item_id] = d.decision
        for a, b in combinations(sorted(by_coder), 2):
            pair = _pair("screening", (a, b), by_coder[a], by_coder[b], multi=False)
            if pair:
                results.append(pair)

    fields = [
        f for f in form.fields if f.type != "text" and (dimensions is None or f.name in dimensions)
    ]
    coded: dict[str, dict[str, dict[str, Any]]] = {}  # coder -> item -> values
    for (item_id, coder), rec in codings.items():
        coded.setdefault(coder, {})[item_id] = rec.values
    for f in fields:
        for a, b in combinations(sorted(coded), 2):
            va = {i: v.get(f.name) for i, v in coded[a].items()}
            vb = {i: v.get(f.name) for i, v in coded[b].items()}
            pair = _pair(f.name, (a, b), va, vb, multi=f.type == "multi")
            if pair:
                results.append(pair)
    return results


def double_sample(items: list[Item], rate: float = 0.2, seed: int = DEFAULT_SEED) -> list[str]:
    """Item ids for double coding: ⌈rate·n⌉ per source (at least one), reproducibly."""
    import math

    if not 0 < rate <= 1:
        raise ValueError("rate must be in (0, 1]")
    by_source: dict[str, list[str]] = {}
    for it in items:
        by_source.setdefault(it.source, []).append(it.id)
    rng = random.Random(seed)
    chosen: list[str] = []
    for source in sorted(by_source):
        ids = sorted(by_source[source])
        k = min(len(ids), max(1, math.ceil(rate * len(ids))))
        chosen += sorted(rng.sample(ids, k))
    return chosen
