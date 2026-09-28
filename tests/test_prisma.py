"""PRISMA flow from run records (Phase 2, T6)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.coding import CodingRecord, append_codings
from msrkit.models import (
    Availability,
    AvailabilityStatus,
    Item,
    ItemKind,
    Manifest,
    Provenance,
    QueryManifestEntry,
    SourceManifestEntry,
)
from msrkit.prisma import build_flow, render_markdown
from msrkit.provenance import save_manifest
from msrkit.screening import ScreeningDecision, append_decisions
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path


def _item(i: int, source: str) -> Item:
    return Item(
        id=f"{source[:2]}{i}",
        source=source,
        kind=ItemKind.THREAD,
        url=f"https://x.example/{source}/{i}",  # type: ignore[arg-type]
        provenance=Provenance(
            run_id="r",
            query_string="q",
            partition="p",
            adapter="a",
            adapter_version="1",
            fetched_at=datetime.now(UTC),
            response_sha256="",
            raw_ref="",
        ),  # fmt: skip
    )


OK = Availability(status=AvailabilityStatus.OK, reason="ok")


def _manifest() -> Manifest:
    def q(items: int, discarded: int = 0, est: int | None = None, cap: bool = False):
        return QueryManifestEntry(
            query_string="q", partitions=1, requests=1, items=items, truncated=cap,
            truncation_reasons=["source_cap"] if cap else [], estimated_total=est,
            discarded=discarded,
        )  # fmt: skip

    return Manifest(
        run_id="run-p",
        msrkit_version="t",
        protocol_path="protocols/v0_rag_agents_testing.yaml",
        protocol_sha256="",
        started_at=datetime.now(UTC),
        sources=[
            SourceManifestEntry(name="github", adapter_version="1",
                                availability=OK,
                                queries=[q(3, est=1003, cap=True)]),
            SourceManifestEntry(name="hackernews", adapter_version="1",
                                availability=OK, queries=[q(2, discarded=3)]),
        ],
    )  # fmt: skip


def _dec(item: str, coder: str, decision: str, crit: list[str] | None = None):
    return ScreeningDecision(
        item_id=item, coder=coder, decision=decision, criteria_met=crit or [],  # type: ignore[arg-type]
        reason="r", decided_at="t", sheet_sha256="h",
    )  # fmt: skip


def _setup(tmp_path: Path) -> list[Item]:
    items = [_item(i, "github") for i in range(3)] + [_item(i, "hackernews") for i in range(2)]
    discards = tmp_path / "discarded.jsonl"
    discards.write_text(
        "\n".join(json.dumps({"reason": r}) for r in ("out_of_window", "out_of_window")) + "\n"
    )
    return items


class TestFlow:
    def test_counts_and_reasons(self, tmp_path: Path) -> None:
        items = _setup(tmp_path)
        pool = items[:4]  # hn1 removed as a duplicate
        decisions = {
            ("gi0", "ana"): _dec("gi0", "ana", "include"),
            ("gi0", "bia"): _dec("gi0", "bia", "include"),
            ("gi1", "ana"): _dec("gi1", "ana", "exclude", ["E1", "I2"]),
            ("gi1", "bia"): _dec("gi1", "bia", "exclude", ["E3"]),
            ("gi2", "ana"): _dec("gi2", "ana", "include"),
            ("gi2", "bia"): _dec("gi2", "bia", "exclude"),
            ("hn1", "ana"): _dec("hn1", "ana", "include"),  # not in pool: ignored
        }
        flow = build_flow(
            "run-p", _manifest(), items, pool,
            {"removed": {"url": 1, "content": 0, "near": 0}},
            tmp_path / "discarded.jsonl", decisions, {"gi0", "gi1"},
        )  # fmt: skip
        assert flow.identified == {"github": 3, "hackernews": 5}
        assert flow.not_retrieved_estimate == {"github": 1000}
        assert flow.filtered == {"out_of_window": 2, "unspecified": 1}
        assert flow.duplicates == {"url": 1, "content": 0, "near": 0}
        assert (flow.screened, flow.excluded, flow.uncertain, flow.pending) == (4, 1, 1, 1)
        assert flow.exclusion_reasons == {"E1": 1, "E3": 1}
        assert (flow.included, flow.coded) == (1, 1)
        assert flow.included_by_source == {"github": 1}

    def test_missing_dedupe_is_na_not_zero(self, tmp_path: Path) -> None:
        items = _setup(tmp_path)
        flow = build_flow("r", None, items, items, None, tmp_path / "none.jsonl", {}, set())
        assert flow.duplicates is None
        assert flow.pending == 5
        md = render_markdown(flow)
        assert "Duplicates: n/a" in md
        assert "flowchart TD" in md


class TestPrismaCommand:
    def test_writes_reports(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        items = _setup(tmp_path)
        ItemStorage(tmp_path).save_items(items, "run-p")
        save_manifest(_manifest(), tmp_path)
        append_decisions(tmp_path, "run-p", [_dec("gi0", "ana", "include")])
        append_codings(
            tmp_path,
            "run-p",
            [CodingRecord(item_id="gi0", coder="ana", values={}, coded_at="t", sheet_sha256="h")],
        )
        r = CliRunner().invoke(app, ["prisma", "--run", "run-p"])
        assert r.exit_code == 0, r.stdout
        assert "Identified 8" in r.stdout and "included 1" in r.stdout
        data = json.loads((tmp_path / "reports/run-p/prisma.json").read_text())
        assert data["coded"] == 1
        assert (tmp_path / "reports/run-p/prisma.md").exists()
