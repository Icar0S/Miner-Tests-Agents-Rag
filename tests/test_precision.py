"""Extraction precision on a stratified sample (Phase 2, V2)."""

from __future__ import annotations

import csv
import json
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.extract import Detection
from msrkit.screening import SheetError
from msrkit.validation import (
    allocate,
    detection_key,
    precision_sample,
    score_precision,
    wilson,
)

if TYPE_CHECKING:
    from pathlib import Path


def _det(i: int, source: str, level: str) -> Detection:
    return Detection(
        item_id=f"it{i}", source=source, entry_id="ragas", entry_type="tool",
        level=level, signal="text" if level == "N1" else "ci",  # type: ignore[arg-type]
        location=f"loc{i}", evidence="e",
    )  # fmt: skip


def _population() -> list[Detection]:
    dets = [_det(i, "github", "N1") for i in range(600)]
    dets += [_det(1000 + i, "github", "N3") for i in range(30)]
    dets += [_det(2000 + i, "stackexchange", "N1") for i in range(370)]
    return dets


class TestAllocation:
    def test_proportional_with_floor(self) -> None:
        alloc = allocate({"a": 600, "b": 30, "c": 370, "d": 1}, 200)
        assert sum(alloc.values()) == 200
        assert alloc["d"] == 1
        assert alloc["a"] > alloc["c"] > alloc["b"] >= 1

    def test_small_population_and_many_strata(self) -> None:
        assert allocate({"a": 3, "b": 2}, 200) == {"a": 3, "b": 2}
        assert allocate({"a": 5, "b": 3, "c": 1}, 2) == {"a": 1, "b": 1, "c": 0}


class TestSample:
    def test_stratified_and_reproducible(self) -> None:
        dets = _population()
        sample, sizes = precision_sample(dets, n=200)
        assert len(sample) == 200
        assert sizes == {"github/N1": 600, "github/N3": 30, "stackexchange/N1": 370}
        again, _ = precision_sample(list(reversed(dets)), n=200)
        assert [detection_key(d) for d in again] == [detection_key(d) for d in sample]
        assert any(d.level == "N3" for d in sample)


class TestScore:
    def _sheet(self, path: Path, rows: list[tuple[str, str]]) -> None:
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["key", "stratum", "correct"])
            w.writerows((str(i), s, c) for i, (s, c) in enumerate(rows))

    def test_weighted_by_population(self, tmp_path: Path) -> None:
        path = tmp_path / "p.csv"
        # stratum A: 4/4 correct, population 900; stratum B: 0/4, population 100
        self._sheet(path, [("gh/N1", "sim")] * 4 + [("gh/N2", "não")] * 4 + [("gh/N2", "")])
        report = score_precision(path, {"gh/N1": 900, "gh/N2": 100})
        assert report.judged == 8 and report.correct == 4
        assert report.weighted_precision == pytest.approx(0.9)
        assert report.by_level == {"N1": 1.0, "N2": 0.0}
        lo, hi = report.ci95
        assert lo < 0.5 < hi

    def test_invalid_marks(self, tmp_path: Path) -> None:
        path = tmp_path / "p.csv"
        self._sheet(path, [("gh/N1", "talvez")])
        with pytest.raises(SheetError):
            score_precision(path)

    def test_wilson_bounds(self) -> None:
        assert wilson(0, 0) == (0.0, 0.0)
        lo, hi = wilson(10, 10)
        assert hi == 1.0 and 0.6 < lo < 0.8


class TestPrecisionCommands:
    def test_sample_then_score(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        runner = CliRunner()
        r = runner.invoke(app, ["precision", "sample", "--run", "run-v"])
        assert r.exit_code == 1  # no detections

        out = tmp_path / "extract" / "run-v"
        out.mkdir(parents=True)
        (out / "detections.jsonl").write_text(
            "\n".join(d.model_dump_json() for d in _population()) + "\n"
        )
        r = runner.invoke(app, ["precision", "sample", "--run", "run-v", "--n", "20"])
        assert r.exit_code == 0, r.stdout
        sheet = tmp_path / "validation" / "run-v" / "precision_sample.csv"
        with open(sheet, encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh, delimiter=";")
            fields, rows = reader.fieldnames, list(reader)
        assert len(rows) == 20
        for row in rows:
            row["correct"] = "yes" if row["level"] == "N3" else "no"
        with open(sheet, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, delimiter=";")
            w.writeheader()
            w.writerows(rows)
        r = runner.invoke(app, ["precision", "score", str(sheet), "--run", "run-v"])
        assert r.exit_code == 0, r.stdout
        report = json.loads((tmp_path / "validation/run-v/precision_report.json").read_text())
        assert report["weighted_precision"] == pytest.approx(30 / 1000)
