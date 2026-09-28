"""PRISMA 2020 flow for a run (RF8), computed from the run's own records.

Every number is traceable to a file:

| Stage                               | Source                                          |
|-------------------------------------|-------------------------------------------------|
| Identified, per source              | items.jsonl + manifest `discarded`              |
| Not retrieved (source caps, est.)   | manifest `estimated_total` of truncated queries |
| Removed by local filters            | runs/<id>/discarded.jsonl, by reason            |
| Duplicates removed                  | items/<id>/dedupe_report.json, by pass          |
| Screened / excluded / uncertain     | screening/<id>/decisions.jsonl (final decision) |
| Exclusion reasons                   | criteria marked by the excluding coders         |
| Included / coded                    | final decisions / coding/<id>/codings.jsonl     |

Duplicates are reported as missing ("n/a") when dedupe has not run, never as zero.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from pathlib import Path

    from msrkit.models import Item, Manifest
    from msrkit.screening import ScreeningDecision


class PrismaFlow(BaseModel):
    run_id: str
    identified: dict[str, int]  # per source: returned by the search (kept + filtered)
    not_retrieved_estimate: dict[str, int]  # per source, from truncated queries
    truncated_queries: int
    filtered: dict[str, int]  # local filter reason -> count
    after_filters: int
    duplicates: dict[str, int] | None  # url/content/near; None when dedupe not run
    screened: int  # the screening pool: deduplicated items when dedupe ran
    excluded: int
    exclusion_reasons: dict[str, int] = {}
    uncertain: int
    pending: int  # no decision yet
    included: int
    included_by_source: dict[str, int] = {}
    coded: int

    @property
    def total_identified(self) -> int:
        return sum(self.identified.values())


def _discard_reasons(path: Path) -> Counter[str]:
    reasons: Counter[str] = Counter()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                reasons[json.loads(line).get("reason") or "unspecified"] += 1
    return reasons


def build_flow(
    run_id: str,
    manifest: Manifest | None,
    items: list[Item],
    pool: list[Item],
    dedupe_report: dict[str, Any] | None,
    discard_file: Path,
    decisions: dict[tuple[str, str], ScreeningDecision],
    coded_ids: set[str],
) -> PrismaFlow:
    kept = Counter(it.source for it in items)
    filtered_by_source: Counter[str] = Counter()
    not_retrieved: Counter[str] = Counter()
    truncated = 0
    for src in manifest.sources if manifest else []:
        for q in src.queries:
            filtered_by_source[src.name] += q.discarded
            if "source_cap" in q.truncation_reasons:
                truncated += 1
                if q.estimated_total is not None:
                    gap = q.estimated_total - q.items - q.discarded
                    not_retrieved[src.name] += max(gap, 0)
    sources = sorted(kept.keys() | filtered_by_source.keys())
    identified = {s: kept[s] + filtered_by_source[s] for s in sources}

    reasons = _discard_reasons(discard_file)
    # The manifest count is authoritative; the file gives the reasons.
    missing = sum(filtered_by_source.values()) - sum(reasons.values())
    if missing > 0:
        reasons["unspecified"] += missing

    duplicates = None
    if dedupe_report is not None:
        duplicates = {k: int(v) for k, v in (dedupe_report.get("removed") or {}).items()}

    from msrkit.screening import final_decisions

    pool_ids = {it.id for it in pool}
    final = {i: d for i, d in final_decisions(decisions).items() if i in pool_ids}
    excl_reasons: Counter[str] = Counter()
    for item_id, decision in final.items():
        if decision != "exclude":
            continue
        marked = {
            c
            for (i, _coder), d in decisions.items()
            if i == item_id and d.decision == "exclude"
            for c in d.criteria_met
            if c.upper().startswith("E")
        }
        for c in sorted(marked) or ["other (reason given)"]:
            excl_reasons[c] += 1
    included = {i for i, d in final.items() if d == "include"}
    by_source = Counter(it.source for it in pool if it.id in included)
    return PrismaFlow(
        run_id=run_id,
        identified=identified,
        not_retrieved_estimate={s: n for s, n in sorted(not_retrieved.items()) if n},
        truncated_queries=truncated,
        filtered=dict(reasons.most_common()),
        after_filters=len(items),
        duplicates=duplicates,
        screened=len(pool),
        excluded=sum(d == "exclude" for d in final.values()),
        exclusion_reasons=dict(sorted(excl_reasons.items())),
        uncertain=sum(d == "uncertain" for d in final.values()),
        pending=len(pool) - len(final),
        included=len(included),
        included_by_source=dict(sorted(by_source.items())),
        coded=len(coded_ids & included),
    )


def render_markdown(flow: PrismaFlow) -> str:
    lines = [f"# PRISMA flow — {flow.run_id}", "", "## Identification", ""]
    lines += ["| Source | Identified | Not retrieved (est., source caps) |", "|---|---:|---:|"]
    for s, n in flow.identified.items():
        lines.append(f"| {s} | {n} | {flow.not_retrieved_estimate.get(s, 0)} |")
    lines.append(f"| **total** | **{flow.total_identified}** | "
                 f"**{sum(flow.not_retrieved_estimate.values())}** |")  # fmt: skip
    lines += ["", f"Queries truncated by a source cap: {flow.truncated_queries}", ""]
    lines += ["## Records removed before screening", ""]
    for reason, n in flow.filtered.items():
        lines.append(f"- Local filter — {reason}: {n}")
    if flow.duplicates is None:
        lines.append("- Duplicates: n/a (run `msrkit dedupe`)")
    else:
        for reason, n in flow.duplicates.items():
            lines.append(f"- Duplicates — {reason}: {n}")
    lines += ["", "## Screening", ""]
    lines.append(f"- Records screened: {str(flow.screened)}")
    lines.append(f"- Excluded: {str(flow.excluded)}")
    for reason, n in flow.exclusion_reasons.items():
        lines.append(f"  - {reason}: {n}")
    lines.append(f"- Uncertain (third coder): {str(flow.uncertain)}")
    lines.append(f"- Not yet screened: {str(flow.pending)}")
    lines += ["", "## Included", ""]
    lines.append(f"- Included: {str(flow.included)}")
    for s, n in flow.included_by_source.items():
        lines.append(f"  - {s}: {n}")
    lines.append(f"- Coded (extraction form): {str(flow.coded)}")
    lines += ["", "```mermaid", render_mermaid(flow), "```", ""]
    return "\n".join(lines)


def render_mermaid(flow: PrismaFlow) -> str:
    dup = "n/a" if flow.duplicates is None else str(sum(flow.duplicates.values()))
    filtered = sum(flow.filtered.values())
    per_source = "<br/>".join(f"{s}: {n}" for s, n in flow.identified.items())
    excl = "<br/>".join(f"{r}: {n}" for r, n in flow.exclusion_reasons.items())
    return "\n".join(
        [
            "flowchart TD",
            f'  A["Records identified: {flow.total_identified}<br/>{per_source}"]',
            f'  B["Removed before screening<br/>local filters: {filtered}<br/>duplicates: {dup}"]',
            f'  C["Records screened: {str(flow.screened)}"]',
            f'  D["Excluded: {str(flow.excluded)}' + (f"<br/>{excl}" if excl else "") + '"]',
            f'  E["Uncertain: {str(flow.uncertain)} · not screened: {str(flow.pending)}"]',
            f'  F["Included: {str(flow.included)}<br/>coded: {str(flow.coded)}"]',
            "  A --> B",
            "  A --> C",
            "  C --> D",
            "  C --> E",
            "  C --> F",
        ]
    )
