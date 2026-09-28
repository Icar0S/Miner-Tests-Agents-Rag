"""Gold set recall (Phase 2, V1)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.extract import Detection
from msrkit.models import Item, ItemKind, Provenance
from msrkit.storage import ItemStorage
from msrkit.validation import GoldSet, load_gold, measure_recall

if TYPE_CHECKING:
    from pathlib import Path


def _item(url: str, source: str = "github", kind: ItemKind = ItemKind.CODE) -> Item:
    return Item(
        id=url[-10:],
        source=source,
        kind=kind,
        url=url,
        title="t",  # type: ignore[arg-type]
        provenance=Provenance(
            run_id="r",
            query_string="q",
            partition="p",
            adapter=source,
            adapter_version="1",
            fetched_at=datetime.now(UTC),
            response_sha256="",
            raw_ref="",
        ),
    )


GOLD = GoldSet.model_validate(
    {
        "version": "1",
        "entries": [
            {"url": "https://github.com/Acme/RAG", "tools": ["ragas", "promptfoo"]},
            {"url": "https://stackoverflow.com/questions/42/how-to-test-rag/"},
            {"url": "https://github.com/missing/repo"},
        ],
    }
)


class TestRecall:
    def test_repo_matches_any_item_and_urls_are_canonicalized(self) -> None:
        items = [
            _item("https://github.com/acme/rag/blob/main/tests/test_x.py"),
            _item(
                "https://stackoverflow.com/questions/42/how-to-test-rag?utm_source=x",
                source="stackexchange",
                kind=ItemKind.THREAD,
            ),
        ]
        dets = [
            Detection(
                item_id="x",
                source="github",
                repo="acme/rag",
                entry_id="ragas",
                entry_type="tool",
                level="N2",
                signal="import",
                location="p",
                evidence="e",
            )
        ]
        report = measure_recall(GOLD, items, dets)
        assert (report.retrieved, report.total) == (2, 3)
        assert report.missed == ["https://github.com/missing/repo"]
        assert report.by_host["github.com"] == (1, 2)
        assert (report.tools_detected, report.tools_expected) == (1, 2)
        assert report.tools_missed == ["https://github.com/Acme/RAG: promptfoo"]

    def test_example_gold_file_loads(self) -> None:
        assert load_gold("protocols/gold_set.example.yaml").entries

    def test_recall_command(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items([_item("https://github.com/acme/rag")], "run-g")
        gold = tmp_path / "gold.yaml"
        gold.write_text(
            "version: '1'\nentries:\n  - url: https://github.com/acme/rag\n"
            "  - url: https://github.com/other/one\n"
        )
        r = CliRunner().invoke(app, ["recall", str(gold), "--run", "run-g"])
        assert r.exit_code == 0, r.stdout
        assert "1/2 (50.0%)" in r.stdout
        assert "missed https://github.com/other/one" in r.stdout
