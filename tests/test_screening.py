"""Screening sheet export/import (Phase 2, T2)."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.extract import Detection
from msrkit.models import Item, ItemKind, Provenance, TermHit
from msrkit.screening import (
    ScreeningCriteria,
    SheetError,
    export_sheet,
    final_decisions,
    import_sheet,
    load_decisions,
    rank_items,
    relevance_score,
)
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path

CRIT = ScreeningCriteria(
    inclusion={"I1": "trata de teste", "I2": "janela"},
    exclusion={"E1": "LLM4Testing", "E5": "tutorial genérico"},
    batch_size=2,
)
PROTO = "protocols/v0_rag_agents_testing.yaml"


def _items(n: int = 3) -> list[Item]:
    return [
        Item(
            id=f"it{i}",
            source="hackernews",
            kind=ItemKind.THREAD,
            url=f"https://x.example/{i}",
            title=f"Item {i}",  # type: ignore[arg-type]
            provenance=Provenance(
                run_id="r",
                query_string="q",
                partition="p",
                adapter="h",
                adapter_version="1",
                fetched_at=datetime.now(UTC),
                response_sha256="",
                raw_ref="",
            ),
        )
        for i in range(n)
    ]


def _fill(path: Path, rows: dict[str, dict[str, str]]) -> None:
    with open(path, encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        fields = reader.fieldnames
        data = list(reader)
    for row in data:
        row.update(rows.get(row["item_id"], {}))
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(data)


class TestSheet:
    def test_export_has_criteria_columns_and_batches(self, tmp_path: Path) -> None:
        path = tmp_path / "s.csv"
        assert export_sheet(_items(), CRIT, path, coder="ana") == 3
        with open(path, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh, delimiter=";"))
        assert {"I1", "I2", "E1", "E5", "decision", "reason", "coder"} <= set(rows[0])
        assert [r["batch"] for r in rows] == ["1", "1", "2"]
        assert rows[0]["coder"] == "ana"

    def test_import_roundtrip_and_blank_rows_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "s.csv"
        export_sheet(_items(), CRIT, path, coder="ana")
        _fill(
            path,
            {
                "it0": {"I1": "x", "I2": "x", "decision": "include"},
                "it1": {"E5": "sim", "decision": "exclude"},
            },
        )
        decisions = import_sheet(path, CRIT, {"it0", "it1", "it2"})
        assert [(d.item_id, d.decision, d.criteria_met) for d in decisions] == [
            ("it0", "include", ["I1", "I2"]),
            ("it1", "exclude", ["E5"]),
        ]

    def test_inconsistent_rows_block_the_whole_import(self, tmp_path: Path) -> None:
        path = tmp_path / "s.csv"
        export_sheet(_items(), CRIT, path, coder="ana")
        _fill(
            path,
            {
                "it0": {"E1": "x", "decision": "include"},
                "it1": {"decision": "exclude"},
                "it2": {"decision": "maybe"},
            },
        )
        with pytest.raises(SheetError) as err:
            import_sheet(path, CRIT, {"it0", "it1", "it2"})
        assert len(err.value.problems) == 3

    def test_final_decision_is_uncertain_on_disagreement(self) -> None:
        from msrkit.screening import ScreeningDecision

        def d(item: str, coder: str, dec: str) -> ScreeningDecision:
            return ScreeningDecision(
                item_id=item,
                coder=coder,
                decision=dec,  # type: ignore[arg-type]
                decided_at="t",
                sheet_sha256="h",
            )

        latest = {
            ("a", "ana"): d("a", "ana", "include"),
            ("a", "bia"): d("a", "bia", "include"),
            ("b", "ana"): d("b", "ana", "include"),
            ("b", "bia"): d("b", "bia", "exclude"),
        }
        assert final_decisions(latest) == {"a": "include", "b": "uncertain"}


class TestScreenCommands:
    def test_export_import_status(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items(_items(), "run-s")
        runner = CliRunner()
        sheet = tmp_path / "ana.csv"
        r = runner.invoke(
            app,
            ["screen", "export", "--run", "run-s", "-p", PROTO, "--coder", "ana", "-o", str(sheet)],
        )
        assert r.exit_code == 0, r.stdout
        _fill(
            sheet,
            {"it0": {"I1": "x", "decision": "include"}, "it1": {"E3": "x", "decision": "exclude"}},
        )
        r = runner.invoke(app, ["screen", "import", str(sheet), "--run", "run-s", "-p", PROTO])
        assert r.exit_code == 0, r.stdout
        assert len(load_decisions(tmp_path, "run-s")) == 2

        r = runner.invoke(app, ["screen", "status", "--run", "run-s"])
        assert "Pending (no decision): 1" in r.stdout

        r = runner.invoke(
            app,
            [
                "screen",
                "export",
                "--run",
                "run-s",
                "-p",
                PROTO,
                "--coder",
                "ana",
                "-o",
                str(tmp_path / "next.csv"),
            ],
        )
        assert "1 items written" in r.stdout  # already-decided items are skipped


class TestRanking:
    def _ranked_items(self) -> list[Item]:
        items = _items(4)
        items[1].concepts = ["rag", "teste"]
        items[1].matched_terms = [
            TermHit(term=t, field="title", context=t) for t in ("rag", "eval")
        ]
        items[2].concepts = ["agente"]
        items[3].tech.has_ci = True
        return items

    def test_score_components(self) -> None:
        items = self._ranked_items()
        assert relevance_score(items[0]) == (0.0, "")
        assert relevance_score(items[1]) == (6.0, "")  # 2 terms + system + testing
        det = Detection(
            item_id="it2", source="github", entry_id="ragas", entry_type="tool",
            level="N3", signal="ci", location="ci.yml", evidence="ragas",
        )  # fmt: skip
        assert relevance_score(items[2], [det]) == (7.0, "N3")

    def test_rank_is_deterministic_and_batches_fixed(self) -> None:
        items = self._ranked_items()
        ranking = rank_items(items, None, batch_size=2)
        order = [r.item_id for r in sorted(ranking.values(), key=lambda r: r.rank)]
        assert order == ["it1", "it2", "it3", "it0"]
        assert [ranking[i].batch for i in order] == [1, 1, 2, 2]
        assert rank_items(list(reversed(items)), None, batch_size=2) == ranking

    def test_export_follows_ranking_and_filters_by_batch(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items(self._ranked_items(), "run-r")
        sheet = tmp_path / "b2.csv"
        r = CliRunner().invoke(
            app,
            ["screen", "export", "--run", "run-r", "-p", PROTO, "-b", "2", "-o", str(sheet)],
        )
        assert r.exit_code == 0, r.stdout
        with open(sheet, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh, delimiter=";"))
        assert rows == []  # protocol batch_size is 25: all four items are in batch 1
        r = CliRunner().invoke(
            app, ["screen", "export", "--run", "run-r", "-p", PROTO, "-o", str(sheet)]
        )
        with open(sheet, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh, delimiter=";"))
        assert [row["item_id"] for row in rows] == ["it1", "it2", "it3", "it0"]
        assert [row["rank"] for row in rows] == ["1", "2", "3", "4"]

    def test_invalid_order_rejected(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items(_items(), "run-o")
        r = CliRunner().invoke(app, ["screen", "export", "--run", "run-o", "--order", "x"])
        assert r.exit_code == 1
