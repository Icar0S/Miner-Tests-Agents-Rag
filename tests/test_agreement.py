"""Inter-coder agreement and the double-coding sample (Phase 2, T5)."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from msrkit.agreement import agreement, cohen_kappa, double_sample, interpret
from msrkit.cli import app
from msrkit.coding import CodingField, CodingForm, CodingRecord, append_codings
from msrkit.models import Item, ItemKind, Provenance
from msrkit.screening import ScreeningDecision, append_decisions
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path

PROTO = "protocols/v0_rag_agents_testing.yaml"
FORM = CodingForm(
    failure_modes={"FP1": "", "AF1": "", "AF2": ""},
    fields=[
        CodingField(name="nivel", type="enum", values=["N1", "N2", "N3"]),
        CodingField(name="modos", type="multi", vocabulary="failure_modes"),
        CodingField(name="nao_cobre", type="bool"),
        CodingField(name="nota", type="text"),
    ],
)


def _rec(item: str, coder: str, **values: object) -> CodingRecord:
    return CodingRecord(item_id=item, coder=coder, values=values, coded_at="t", sheet_sha256="h")


def _item(i: int, source: str = "hackernews") -> Item:
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


class TestKappa:
    def test_textbook_value(self) -> None:
        # 2x2 table: yes/yes 20, yes/no 5, no/yes 10, no/no 15 -> κ = 0.4
        a = ["y"] * 25 + ["n"] * 25
        b = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
        assert cohen_kappa(a, b) == pytest.approx(0.4)

    def test_perfect_and_undefined(self) -> None:
        assert cohen_kappa(["a", "b"], ["a", "b"]) == 1.0
        assert cohen_kappa(["a", "a"], ["a", "a"]) is None
        assert cohen_kappa([], []) is None
        with pytest.raises(ValueError):
            cohen_kappa(["a"], [])

    def test_interpretation_bands(self) -> None:
        assert interpret(None) == "undefined"
        assert interpret(-0.1) == "poor"
        assert interpret(0.5) == "moderate"
        assert interpret(0.81) == "almost perfect"


class TestAgreement:
    def test_dimensions_pairs_and_disagreements(self) -> None:
        codings = {
            ("i1", "ana"): _rec("i1", "ana", nivel="N1", modos=["FP1"], nao_cobre=True, nota="x"),
            ("i1", "bia"): _rec("i1", "bia", nivel="N1", modos=["FP1"], nao_cobre=None, nota="y"),
            ("i2", "ana"): _rec("i2", "ana", nivel="N2", modos=["AF1", "AF2"], nao_cobre=False),
            ("i2", "bia"): _rec("i2", "bia", nivel="N3", modos=["AF1"], nao_cobre=False),
            ("i3", "ana"): _rec("i3", "ana", nivel="N3", modos=[]),  # ana only
        }
        results = {r.dimension: r for r in agreement(FORM, codings)}
        assert set(results) == {"nivel", "modos", "nao_cobre"}  # no κ for text
        assert results["nivel"].n == 2
        assert results["nivel"].disagreements == ["i2"]
        assert results["nao_cobre"].disagreements == ["i1"]  # blank is a category
        modos = results["modos"]
        assert modos.percent == 0.5
        assert modos.per_value["FP1"] == 1.0
        assert set(modos.per_value) == {"FP1", "AF1", "AF2"}

    def test_screening_and_dimension_filter(self) -> None:
        decisions = {
            (i, c): ScreeningDecision(item_id=i, coder=c, decision=d, decided_at="t",
                                      sheet_sha256="h")
            for (i, c, d) in [("a", "x", "include"), ("a", "y", "include"),
                              ("b", "x", "exclude"), ("b", "y", "include")]
        }  # fmt: skip
        results = agreement(FORM, {}, decisions, dimensions=["nivel"])
        assert [r.dimension for r in results] == ["screening"]
        assert results[0].percent == 0.5


class TestDoubleSample:
    def test_stratified_reproducible_at_least_one(self) -> None:
        items = [_item(i) for i in range(10)] + [_item(0, "github")]
        ids = double_sample(items, rate=0.2)
        assert len(ids) == 3  # ceil(0.2*10)=2 hackernews + 1 github
        assert "gi0" in ids
        assert double_sample(list(reversed(items)), rate=0.2) == ids
        with pytest.raises(ValueError):
            double_sample(items, rate=0)

    def test_protocol_defaults(self) -> None:
        from msrkit.config import load_protocol

        form = load_protocol(PROTO).coding
        assert form.double_coding_rate == 0.2
        assert form.agreement == ["sistema", "nivel_evidencia", "modos_falha", "tipo_oraculo"]
        with pytest.raises(ValueError, match="unknown fields"):
            CodingForm(fields=[CodingField(name="a")], agreement=["b"])


class TestAgreementCommands:
    def test_sample_export_and_agreement(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        items = [_item(i) for i in range(5)]
        ItemStorage(tmp_path).save_items(items, "run-k")
        runner = CliRunner()

        r = runner.invoke(app, ["agreement", "--run", "run-k", "-p", PROTO])
        assert "No item was coded by two coders" in r.stdout

        r = runner.invoke(app, ["coding", "sample", "--run", "run-k", "-p", PROTO])
        assert r.exit_code == 1  # nothing included yet
        append_decisions(
            tmp_path,
            "run-k",
            [
                ScreeningDecision(item_id=it.id, coder=c, decision="include", decided_at="t",
                                  sheet_sha256="h")
                for it in items for c in ("ana", "bia")
            ],
        )  # fmt: skip
        r = runner.invoke(app, ["coding", "sample", "--run", "run-k", "-p", PROTO])
        assert r.exit_code == 0, r.stdout
        sample = json.loads((tmp_path / "coding/run-k/double_sample.json").read_text())
        assert len(sample["item_ids"]) == 1 and sample["eligible"] == 5

        sheet = tmp_path / "s.csv"
        export = ["coding", "export", "--run", "run-k", "-p", PROTO, "--double-sample"]
        r = runner.invoke(app, [*export, "-o", str(sheet)])
        assert "1 items written" in r.stdout

        target = sample["item_ids"][0]
        base = {"sistema": ["rag"], "evidencia_textual": "e", "confianca": "alta"}
        append_codings(
            tmp_path,
            "run-k",
            [
                _rec(target, "ana", **base, nivel_evidencia="N1", modos_falha=["FP1"]),
                _rec(target, "bia", **base, nivel_evidencia="N2", modos_falha=["FP1"]),
            ],
        )
        r = runner.invoke(app, ["agreement", "--run", "run-k", "-p", PROTO])
        assert r.exit_code == 0, r.stdout
        assert "nivel_evidencia" in r.stdout and "screening" in r.stdout
        with open(tmp_path / "coding/run-k/disagreements.csv", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh, delimiter=";"))
        assert rows == [
            {"item_id": target, "dimension": "nivel_evidencia", "coder_a": "ana",
             "value_a": "N1", "coder_b": "bia", "value_b": "N2"}
        ]  # fmt: skip
