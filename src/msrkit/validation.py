"""Validation of the instrument itself (Protocol E2 v2, §10.3 and §12.3).

Recall against a gold set curated *before* collection: which known artifacts the
miner retrieved, and — when the gold entry lists the tools it is known to use —
which of those the extractor detected.

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
