"""Tool/method extraction with evidence levels (Phase 2, B2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.enrich import RepoSignals
from msrkit.extract import Extractor, dependency_names, summarize
from msrkit.gazetteer import Gazetteer
from msrkit.models import Item, ItemKind, Provenance
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path

GAZ = Gazetteer.model_validate(
    {
        "version": "t",
        "tools": [
            {
                "id": "ragas",
                "name": "Ragas",
                "family": "eval",
                "python_imports": ["ragas"],
                "packages": ["ragas"],
            },
            {
                "id": "promptfoo",
                "name": "promptfoo",
                "family": "eval",
                "packages": ["promptfoo"],
                "config_files": ["promptfooconfig.yaml"],
                "cli": ["promptfoo eval"],
            },
            {
                "id": "deepeval",
                "name": "DeepEval",
                "family": "eval",
                "python_imports": ["deepeval"],
                "cli": ["deepeval test run"],
            },
        ],
        "methods": [{"id": "llm-as-judge", "name": "LLM-as-a-judge", "aliases": ["LLM judge"]}],
    }
)


def _item(
    url: str,
    kind: ItemKind = ItemKind.REPO,
    title: str = "t",
    body: str | None = None,
    query: str = "q",
    path: str | None = None,
    source: str = "github",
) -> Item:
    it = Item(
        id=url,
        source=source,
        kind=kind,
        url=url,
        title=title,
        body=body,  # type: ignore[arg-type]
        provenance=Provenance(
            run_id="r",
            query_string=query,
            partition="p",
            adapter=source,
            adapter_version="1",
            fetched_at=datetime.now(UTC),
            response_sha256="",
            raw_ref="",
        ),
    )
    it.tech.path = path
    return it


def _sig(months: int, contributors: int) -> RepoSignals:
    return RepoSignals(
        repo="acme/rag",
        has_ci=True,
        workflows={".github/workflows/eval.yml": "steps:\n  - run: npx promptfoo eval -c x\n"},
        manifests={"requirements.txt": "ragas==0.2.1\nnumpy\n"},
        config_paths=["promptfooconfig.yaml"],
        contributors=contributors,
        commit_months=[f"2024-0{i + 1}" for i in range(months)],
    )


class TestSignals:
    def test_text_mentions_are_n1(self) -> None:
        it = _item(
            "https://news.example/1",
            ItemKind.THREAD,
            title="Why Ragas beats LLM judge",
            source="hackernews",
        )
        dets = Extractor(GAZ).from_item(it)
        assert {(d.entry_id, d.level, d.signal) for d in dets} == {
            ("ragas", "N1", "text"),
            ("llm-as-judge", "N1", "text"),
        }

    def test_code_hit_from_import_query_is_n2(self) -> None:
        it = _item(
            "https://github.com/acme/rag/blob/main/tests/test_x.py",
            ItemKind.CODE,
            title="test_x.py",
            query='kind:code "from ragas import" language:Python',
            path="tests/test_x.py",
        )
        dets = Extractor(GAZ).from_item(it)
        assert [(d.entry_id, d.level, d.signal, d.repo) for d in dets] == [
            ("ragas", "N2", "import", "acme/rag")
        ]

    def test_ci_invocation_is_n3_only_when_sustained(self) -> None:
        repo_item = _item("https://github.com/acme/rag")
        sustained = Extractor(GAZ).from_repo(_sig(months=3, contributors=4), repo_item)
        ci = [d for d in sustained if d.signal == "ci"]
        assert [(d.entry_id, d.level) for d in ci] == [("promptfoo", "N3")]
        assert "npx promptfoo eval" in ci[0].evidence

        young = Extractor(GAZ).from_repo(_sig(months=1, contributors=4), repo_item)
        assert [d.level for d in young if d.signal == "ci"] == ["N2"]
        solo = Extractor(GAZ).from_repo(_sig(months=5, contributors=1), repo_item)
        assert [d.level for d in solo if d.signal == "ci"] == ["N2"]

    def test_dependency_and_config_are_n2(self) -> None:
        dets = Extractor(GAZ).from_repo(_sig(3, 4), _item("https://github.com/acme/rag"))
        assert ("ragas", "dependency", "ragas==0.2.1") in {
            (d.entry_id, d.signal, d.evidence) for d in dets
        }
        assert ("promptfoo", "config") in {(d.entry_id, d.signal) for d in dets}

    def test_dependency_parsing(self) -> None:
        assert set(
            dependency_names(
                "pyproject.toml", '[project]\ndependencies = ["ragas>=0.1", "deep_eval[x]==1"]\n'
            )
        ) >= {"ragas", "deep-eval"}
        assert set(
            dependency_names("package.json", '{"devDependencies": {"promptfoo": "^0.9"}}')
        ) == {"promptfoo"}
        assert set(dependency_names("requirements-dev.txt", "# c\nRagas==1\n-e .\n")) == {"ragas"}


class TestSummaryAndCommand:
    def test_each_unit_counts_once_at_its_highest_level(self) -> None:
        repo_item = _item("https://github.com/acme/rag")
        text = _item("https://github.com/acme/rag/issues/1", ItemKind.ISSUE, title="Ragas fails")
        dets = Extractor(GAZ).run([text, repo_item], {"acme/rag": _sig(3, 4)})
        table = summarize(dets)
        assert table["promptfoo"] == {"N1": 0, "N2": 0, "N3": 1}
        assert table["ragas"] == {"N1": 0, "N2": 1, "N3": 0}  # text N1 superseded by dependency

    def test_extract_command_writes_detections(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items(
            [
                _item(
                    "https://news.example/1",
                    ItemKind.THREAD,
                    title="Ragas in CI",
                    source="hackernews",
                )
            ],
            "run-x",
        )
        result = CliRunner().invoke(
            app, ["extract", "--run", "run-x", "-p", "protocols/v0_rag_agents_testing.yaml"]
        )
        assert result.exit_code == 0, result.stdout
        assert (tmp_path / "extract" / "run-x" / "detections.csv").exists()
        assert "ragas" in (tmp_path / "extract" / "run-x" / "detections.jsonl").read_text()
