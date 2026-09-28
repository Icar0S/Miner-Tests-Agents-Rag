"""Analyses A1–A6 (Phase 2, N1–N5)."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from msrkit.analysis import (
    Unit,
    binomial_greater,
    consensus,
    cooccurrence,
    frequency,
    units_from_codings,
    units_from_detections,
)
from msrkit.cli import app
from msrkit.coding import CodingRecord, append_codings
from msrkit.extract import Detection
from msrkit.models import Item, ItemKind, Provenance
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path


def _item(iid: str, url: str, source: str = "github", concepts: list[str] | None = None) -> Item:
    return Item(
        id=iid,
        source=source,
        kind=ItemKind.REPO,
        url=url,  # type: ignore[arg-type]
        concepts=concepts or [],
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


def _det(item: str, entry: str, level: str = "N1", kind: str = "tool", repo: str | None = None):
    return Detection(
        item_id=item, source="github", repo=repo, entry_id=entry, entry_type=kind,  # type: ignore[arg-type]
        level=level, signal="text" if level == "N1" else "ci",  # type: ignore[arg-type]
        location="x", evidence="e",
    )  # fmt: skip


def _rec(item: str, coder: str, **values: object) -> CodingRecord:
    return CodingRecord(item_id=item, coder=coder, values=values, coded_at="t", sheet_sha256="h")


ITEMS = [
    _item("r1", "https://github.com/o/a", concepts=["rag", "teste"]),
    _item("i1", "https://github.com/o/a/issues/1", concepts=["agente"]),
    _item("r2", "https://github.com/o/b", concepts=["rag"]),
    _item("s1", "https://stackoverflow.com/q/1", source="stackexchange", concepts=["agente"]),
]
DETS = [
    _det("r1", "ragas", "N3", repo="o/a"),
    _det("i1", "ragas", "N1", repo="o/a"),
    _det("i1", "llm-judge", kind="method", repo="o/a"),
    _det("r2", "ragas", "N2", repo="o/b"),
    _det("r2", "deepeval", "N1", repo="o/b"),
    _det("s1", "deepeval"),
    _det("s1", "llm-judge", kind="method"),
]


class TestUnits:
    def test_repository_items_merge_into_one_unit(self) -> None:
        units = {u.id: u for u in units_from_detections(ITEMS, DETS)}
        assert set(units) == {"o/a", "o/b", "s1"}
        assert units["o/a"].tools == {"ragas": "N3"}  # best level wins
        assert units["o/a"].systems == ["agente", "rag"]
        assert units["o/a"].methods == ["llm-judge"]

    def test_consensus(self) -> None:
        a = _rec("x", "ana", nivel="N2", modos=["FP1", "AF1"], ok=True)
        b = _rec("x", "bia", nivel="N3", modos=["AF1"], ok=True)
        assert consensus([a, b]) == {"nivel": None, "modos": ["AF1"], "ok": True}
        assert consensus([a]) == a.values
        assert consensus([]) == {}

    def test_units_from_codings(self) -> None:
        codings = {
            ("r1", "ana"): _rec("r1", "ana", sistema=["rag"], nivel_evidencia="N2",
                                ferramentas=["ragas"], metodos=[], modos_falha=["FP2"],
                                tipo_oraculo="referencia", agregacao="atomico"),
            ("i1", "ana"): _rec("i1", "ana", sistema=["agente"], nivel_evidencia=None,
                                ferramentas=["promptfoo"], metodos=["llm-judge"],
                                modos_falha=["AF1"]),
        }  # fmt: skip
        (unit,) = units_from_codings(ITEMS, codings)
        assert unit.id == "o/a"
        assert unit.systems == ["agente", "rag"]
        assert unit.tools == {"ragas": "N2", "promptfoo": "N1"}
        assert unit.failure_modes == ["AF1", "FP2"]
        assert unit.oracle == "referencia"


class TestFrequency:
    def test_counts_by_level_system_and_source(self) -> None:
        rows = {r.entry: r for r in frequency(units_from_detections(ITEMS, DETS))}
        ragas = rows["ragas"]
        assert (ragas.units, ragas.n1, ragas.n2, ragas.n3) == (2, 0, 1, 1)
        assert (ragas.rag, ragas.agente) == (2, 1)
        assert rows["deepeval"].sources == {"github": 1, "stackexchange": 1}
        assert rows["llm-judge"].kind == "method"

    def test_cooccurrence_and_lift(self) -> None:
        units = [
            Unit(id="a", source="s", tools={"t1": "N1"}, methods=["m1"]),
            Unit(id="b", source="s", tools={"t1": "N1"}, methods=["m1"]),
            Unit(id="c", source="s", tools={"t2": "N1"}, methods=[]),
            Unit(id="d", source="s", tools={"t1": "N1", "t2": "N1"}),
        ]
        (pair,) = cooccurrence(units, "tool-method")
        assert (pair.a, pair.b, pair.together) == ("t1", "m1", 2)
        assert pair.lift == pytest.approx(2 * 4 / (3 * 2), abs=1e-4)
        assert pair.jaccard == pytest.approx(2 / 3, abs=1e-4)
        (tt,) = cooccurrence(units, "tool-tool")
        assert (tt.a, tt.b) == ("t1", "t2")
        with pytest.raises(ValueError):
            cooccurrence(units, "x")

    def test_binomial(self) -> None:
        assert binomial_greater(10, 10) == pytest.approx(0.5**10)
        assert binomial_greater(0, 5) == pytest.approx(1.0)


class TestAnalyzeFrequencyCommand:
    def test_writes_csv(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items(ITEMS, "run-a")
        runner = CliRunner()
        r = runner.invoke(app, ["analyze", "frequency", "--run", "run-a"])
        assert r.exit_code == 1  # no detections yet
        out = tmp_path / "extract" / "run-a"
        out.mkdir(parents=True)
        (out / "detections.jsonl").write_text("\n".join(d.model_dump_json() for d in DETS))
        r = runner.invoke(app, ["analyze", "frequency", "--run", "run-a"])
        assert r.exit_code == 0, r.stdout
        path = tmp_path / "reports/run-a/analysis/frequency_detections.csv"
        with open(path, encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh, delimiter=";"))
        assert rows[0]["kind"] == "tool"  # ties: tools before methods
        assert {r["entry"] for r in rows} == {"ragas", "deepeval", "llm-judge"}
        r = runner.invoke(app, ["analyze", "frequency", "--run", "run-a", "--basis", "coding"])
        assert r.exit_code == 1  # no codings
        rec = _rec("s1", "ana", sistema=["agente"], ferramentas=["deepeval"], metodos=[])
        append_codings(tmp_path, "run-a", [rec])
        r = runner.invoke(app, ["analyze", "frequency", "--run", "run-a", "--basis", "coding"])
        assert r.exit_code == 0, r.stdout
        assert (tmp_path / "reports/run-a/analysis/frequency_coding.csv").exists()
