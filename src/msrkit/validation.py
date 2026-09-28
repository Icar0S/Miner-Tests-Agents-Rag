"""Validation of the instrument itself (Protocol E2 v2, §10.3 and §12.3).

Recall against a gold set curated *before* collection: which known artifacts the
miner retrieved, and — when the gold entry lists the tools it is known to use —
which of those the extractor detected.

Precision on a stratified sample of detections (default 200, strata = source ×
evidence level): the sheet is judged manually (`correct` = yes/no) and the
estimate weights each stratum by its population.

Gold set file (YAML):

    version: "1"
    entries:
      - url: https://github.com/owner/repo     # repository: any item of it counts
        tools: [ragas, promptfoo]              # optional: expected detections
        note: "why it is in the gold set"
      - url: https://stackoverflow.com/questions/12345

A repository URL (github.com/owner/repo) is recalled when any item of that
repository was collected; any other URL must match an item URL after
canonicalization.
"""

from __future__ import annotations

import csv
import hashlib
import math
import random
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel

from msrkit.dedupe import canonicalize_url
from msrkit.enrich import repo_of

if TYPE_CHECKING:
    from msrkit.extract import Detection
    from msrkit.models import Item


class GoldEntry(BaseModel):
    url: str
    tools: list[str] = []
    note: str = ""


class GoldSet(BaseModel):
    version: str
    entries: list[GoldEntry]


def load_gold(path: str | Path) -> GoldSet:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Gold set not found: {p}")
    return GoldSet.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))


def _gold_repo(url: str) -> str | None:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if (parsed.hostname or "").lower() == "github.com" and len(parts) == 2:
        return f"{parts[0]}/{parts[1]}".lower()
    return None


class RecallReport(BaseModel):
    total: int
    retrieved: int
    missed: list[str]
    by_host: dict[str, tuple[int, int]]  # host -> (retrieved, total)
    tools_expected: int
    tools_detected: int
    tools_missed: list[str]  # "url: tool"

    @property
    def recall(self) -> float:
        return self.retrieved / self.total if self.total else 0.0

    @property
    def detection_recall(self) -> float:
        return self.tools_detected / self.tools_expected if self.tools_expected else 0.0


def measure_recall(
    gold: GoldSet, items: list[Item], detections: list[Detection] | None = None
) -> RecallReport:
    urls = {canonicalize_url(str(it.url)).lower(): it for it in items}
    repos: dict[str, list[Item]] = {}
    for it in items:
        repo = repo_of(it)
        if repo:
            repos.setdefault(repo.lower(), []).append(it)

    found_by_item: dict[str, set[str]] = {}
    found_by_repo: dict[str, set[str]] = {}
    for d in detections or []:
        found_by_item.setdefault(d.item_id, set()).add(d.entry_id)
        if d.repo:
            found_by_repo.setdefault(d.repo.lower(), set()).add(d.entry_id)

    retrieved = 0
    missed: list[str] = []
    by_host: dict[str, list[int]] = {}
    expected = detected = 0
    tools_missed: list[str] = []
    for entry in gold.entries:
        host = (urlparse(entry.url).hostname or "?").lower()
        counts = by_host.setdefault(host, [0, 0])
        counts[1] += 1
        repo = _gold_repo(entry.url)
        if repo is not None:
            hit = repo in repos
            found = found_by_repo.get(repo, set())
        else:
            item = urls.get(canonicalize_url(entry.url).lower())
            hit = item is not None
            found = found_by_item.get(item.id, set()) if item else set()
        if hit:
            retrieved += 1
            counts[0] += 1
        else:
            missed.append(entry.url)
        for tool in entry.tools:
            expected += 1
            if tool in found:
                detected += 1
            else:
                tools_missed.append(f"{entry.url}: {tool}")

    return RecallReport(
        total=len(gold.entries),
        retrieved=retrieved,
        missed=missed,
        by_host={h: (c[0], c[1]) for h, c in sorted(by_host.items())},
        tools_expected=expected,
        tools_detected=detected,
        tools_missed=tools_missed,
    )


# -- precision (§12.3) --------------------------------------------------------

PRECISION_COLUMNS = [
    "key", "stratum", "item_id", "source", "level", "signal", "entry_id", "entry_type",
    "location", "evidence", "url", "title", "correct", "note",
]  # fmt: skip
PRECISION_SEED = 20260928


def detection_key(d: Detection) -> str:
    """Stable id of a detection (the same detection gets the same key on re-extraction)."""
    raw = "|".join([d.item_id, d.entry_id, d.signal, d.level, d.location])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def stratum_of(d: Detection) -> str:
    return f"{d.source}/{d.level}"


def allocate(sizes: dict[str, int], n: int) -> dict[str, int]:
    """Proportional allocation with at least one unit per non-empty stratum.

    Largest remainders distribute what rounding leaves; never more than a stratum holds.
    """
    total = sum(sizes.values())
    if n >= total:
        return dict(sizes)
    alloc = {s: min(size, 1) for s, size in sizes.items()}
    rest = n - sum(alloc.values())
    if rest <= 0:  # more strata than the sample: keep the largest strata
        keep = sorted(sizes, key=lambda s: (-sizes[s], s))[:n]
        return {s: int(s in keep) for s in sizes}
    quotas = {s: rest * sizes[s] / total for s in sizes}
    for s in sizes:
        alloc[s] += min(int(quotas[s]), sizes[s] - alloc[s])
    left = n - sum(alloc.values())
    for s in sorted(sizes, key=lambda s: (-(quotas[s] - int(quotas[s])), s)):
        if left <= 0:
            break
        if alloc[s] < sizes[s]:
            alloc[s] += 1
            left -= 1
    return alloc


def precision_sample(
    detections: list[Detection], n: int = 200, seed: int = PRECISION_SEED
) -> tuple[list[Detection], dict[str, int]]:
    """Stratified random sample of detections; returns the sample and stratum sizes."""
    strata: dict[str, list[Detection]] = {}
    for d in sorted(detections, key=detection_key):
        strata.setdefault(stratum_of(d), []).append(d)
    sizes = {s: len(ds) for s, ds in sorted(strata.items())}
    alloc = allocate(sizes, n)
    rng = random.Random(seed)
    sample: list[Detection] = []
    for s in sorted(strata):
        sample += rng.sample(strata[s], alloc[s])
    return sample, sizes


def export_precision_sheet(sample: list[Detection], items: dict[str, Item], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PRECISION_COLUMNS, delimiter=";")
        writer.writeheader()
        for d in sample:
            item = items.get(d.item_id)
            writer.writerow(
                {
                    "key": detection_key(d),
                    "stratum": stratum_of(d),
                    **d.model_dump(include={"item_id", "source", "level", "signal",
                                            "entry_id", "entry_type", "location", "evidence"}),
                    "url": str(item.url) if item else "",
                    "title": (item.title or "") if item else "",
                }
            )  # fmt: skip
    return len(sample)


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


class StratumPrecision(BaseModel):
    stratum: str
    population: int
    judged: int
    correct: int

    @property
    def precision(self) -> float:
        return self.correct / self.judged if self.judged else 0.0


class PrecisionReport(BaseModel):
    strata: list[StratumPrecision]
    judged: int
    correct: int
    weighted_precision: float  # Σ W_h · p_h over judged strata (population weights)
    ci95: tuple[float, float]  # Wilson, on the unweighted sample
    by_level: dict[str, float]  # weighted within each level


def score_precision(path: Path, sizes: dict[str, int] | None = None) -> PrecisionReport:
    """Read a judged precision sheet (`correct` = yes/no) and compute precision.

    `sizes` are the stratum populations (from the sample record); without them the
    sheet's own counts weight the strata. Rows with an empty `correct` are skipped;
    any other value that is neither yes nor no raises SheetError.
    """
    from msrkit.coding import FALSE_MARKS, _fold
    from msrkit.screening import TRUE_MARKS, SheetError

    text = path.read_bytes().decode("utf-8-sig")
    delim = ";" if text.split("\n", 1)[0].count(";") else ","
    judged: dict[str, list[bool]] = {}
    problems: list[str] = []
    for line_no, row in enumerate(csv.DictReader(text.splitlines(), delimiter=delim), start=2):
        mark = _fold(row.get("correct") or "")
        if not mark:
            continue
        if mark in TRUE_MARKS:
            judged.setdefault(row["stratum"], []).append(True)
        elif mark in FALSE_MARKS:
            judged.setdefault(row["stratum"], []).append(False)
        else:
            problems.append(f"line {line_no}: correct must be yes/no, got '{row['correct']}'")
    if problems:
        raise SheetError(problems)

    pops = sizes or {s: len(v) for s, v in judged.items()}
    strata = [
        StratumPrecision(stratum=s, population=pops.get(s, len(v)), judged=len(v), correct=sum(v))
        for s, v in sorted(judged.items())
    ]

    def weighted(rows: list[StratumPrecision]) -> float:
        total = sum(r.population for r in rows)
        return sum(r.population * r.precision for r in rows) / total if total else 0.0

    levels = sorted({r.stratum.rsplit("/", 1)[-1] for r in strata})
    n = sum(r.judged for r in strata)
    ok = sum(r.correct for r in strata)
    return PrecisionReport(
        strata=strata,
        judged=n,
        correct=ok,
        weighted_precision=round(weighted(strata), 4),
        ci95=tuple(round(x, 4) for x in wilson(ok, n)),  # type: ignore[arg-type]
        by_level={
            lv: round(weighted([r for r in strata if r.stratum.endswith(f"/{lv}")]), 4)
            for lv in levels
        },
    )
