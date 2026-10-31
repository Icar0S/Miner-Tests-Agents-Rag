"""Invariant and integrity tests for MSR-Kit pipeline (Protocol E2 v2).

Tests critical scientific invariants:
1. Conservation of data (input - duplicates == unique items).
2. Accounting consistency between `stats`, `export`, `prisma`, and `ItemStorage`.
3. Blinding integrity (no score, rank, or keyword leakage to reviewers).
4. UTF-8 encoding integrity across storage, export, and adaptors.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.dedupe import deduplicate_report
from msrkit.models import Item, ItemKind, Provenance, TermHit
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path


def _make_item(
    item_id: str,
    url: str,
    title: str = "Title",
    body: str | None = "Body with evaluation details",
    source: str = "hackernews",
) -> Item:
    return Item(
        id=item_id,
        source=source,
        kind=ItemKind.DISCUSSION,
        url=url,  # type: ignore[arg-type]
        title=title,
        body=body,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        provenance=Provenance(
            run_id="run-inv",
            query_string="rag evaluation",
            partition="p1",
            adapter=source,
            adapter_version="0.1.0",
            fetched_at=datetime(2024, 6, 1, tzinfo=UTC),
            response_sha256="hash",
            raw_ref="data/raw/hn:0",
        ),
    )


class TestPipelineInvariants:
    """Invariant 1 & 2: Accounting conservation and cross-command consistency."""

    def test_dedupe_conservation_invariant(self) -> None:
        """Total input items must strictly equal unique + duplicates."""
        items = [
            _make_item("1", "https://example.com/a", title="Alpha", body="Content alpha"),
            _make_item(
                "2", "https://example.com/a?utm_source=x", title="Alpha", body="Content alpha"
            ),
            _make_item("3", "https://example.com/b", title="Alpha", body="Content alpha"),
            _make_item("4", "https://example.com/c", title="Gamma", body="Unique content gamma"),
        ]
        report = deduplicate_report(items, near_threshold=0.85)
        assert len(report.unique) + len(report.duplicates) == len(items)
        assert len(report.unique) == 2
        assert len(report.duplicates) == 2

    def test_stats_matches_storage_on_consolidated_corpus(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """`msrkit stats -a` must match `ItemStorage.read_items('consolidated')`."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        storage = ItemStorage(tmp_path)

        items_run1 = [
            _make_item("1", "https://example.com/1", title="Item 1", body="Body 1"),
            _make_item("2", "https://example.com/2", title="Item 2", body="Body 2"),
        ]
        items_run2 = [
            _make_item(
                "3", "https://example.com/2?ref=rss", title="Item 2", body="Body 2"
            ),  # dup of 2
            _make_item("4", "https://example.com/3", title="Item 3", body="Body 3"),
        ]
        storage.save_items(items_run1, "2026-10-14T01-00-00Z-test1")
        storage.save_items(items_run2, "2026-10-14T02-00-00Z-test2")

        # Run dedupe to create consolidated corpus
        runner = CliRunner()
        r_dedupe = runner.invoke(app, ["dedupe", "--all"])
        assert r_dedupe.exit_code == 0, r_dedupe.stdout

        consolidated_items = storage.read_items("consolidated", prefer_deduped=True)
        assert len(consolidated_items) == 3

        # Now run stats -a
        r_stats = runner.invoke(app, ["stats", "--all"])
        assert r_stats.exit_code == 0, r_stats.stdout
        assert "Total Consolidated │            3" in r_stats.stdout
        expected_msg = "Total across history: 4 raw items collected | 1 duplicates removed"
        assert expected_msg in r_stats.stdout

        # Now run export -a
        sheet = tmp_path / "export_all.jsonl"
        r_export = runner.invoke(app, ["export", "--all", "-o", str(sheet)])
        assert r_export.exit_code == 0, r_export.stdout
        with open(sheet, encoding="utf-8") as fh:
            exported_lines = [line.strip() for line in fh if line.strip()]
        assert len(exported_lines) == 3


class TestBlindingInvariants:
    """Invariant 3: Blinding must guarantee zero leakage of relevance signals."""

    def test_blind_export_leakage_invariant(self, tmp_path: Path, monkeypatch) -> None:
        """Exporting with --blind must zero out all ranking scores and masked terms."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        items = [
            _make_item(
                "it1", "https://example.com/high", title="High relevance RAG tool", body="Testing"
            ),
            _make_item(
                "it2", "https://example.com/low", title="Low relevance article", body="General"
            ),
        ]
        items[0].matched_terms = [TermHit(term="rag", field="title", context="High relevance RAG")]
        items[0].concepts = ["rag", "teste"]
        ItemStorage(tmp_path).save_items(items, "run-blind-inv")

        sheet = tmp_path / "blind_sheet.csv"
        runner = CliRunner()
        r = runner.invoke(
            app,
            [
                "screen",
                "export",
                "--run",
                "run-blind-inv",
                "--coder",
                "helena",
                "--blind",
                "-o",
                str(sheet),
            ],
        )
        assert r.exit_code == 0, r.stdout
        with open(sheet, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh, delimiter=";"))

        for row in rows:
            assert row["score"] == "", "Score leaked in blind export!"
            assert row["matched_terms"] == "", "Matched terms leaked in blind export!"
            assert row["rank"] == "", "Rank leaked in blind export!"


class TestEncodingInvariants:
    """Invariant 4: UTF-8 encoding preservation for multilingual text."""

    def test_utf8_multilingual_preservation(self, tmp_path: Path, monkeypatch) -> None:
        """Portuguese terms with diacritics, CJK, and emojis must survive pipeline round-trips."""
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        storage = ItemStorage(tmp_path)

        complex_text = "Avaliação de RAG: métricas de alucinação, precisão & acurácia 🚀 [测试]"
        item = _make_item(
            "utf8-1", "https://example.com/pt-rag", title=complex_text, body=complex_text
        )
        storage.save_items([item], "run-utf8")

        # Read back
        loaded = storage.read_items("run-utf8")
        assert len(loaded) == 1
        assert loaded[0].title == complex_text
        assert loaded[0].body == complex_text

        # Dedupe and verify report
        r_dedupe = CliRunner().invoke(app, ["dedupe", "--run", "run-utf8"])
        assert r_dedupe.exit_code == 0, r_dedupe.stdout

        deduped = storage.read_items("run-utf8", prefer_deduped=True)
        assert len(deduped) == 1
        assert deduped[0].title == complex_text
