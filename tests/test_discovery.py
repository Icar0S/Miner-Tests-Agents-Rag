"""Open discovery pass: LDA topics and k-means clusters (Phase 2, N4)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.discovery import catalog_origin, discover, is_candidate, known_vocabulary
from msrkit.gazetteer import Gazetteer, MethodEntry, ToolEntry
from msrkit.models import Item, ItemKind, Provenance
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path

pytest.importorskip("sklearn")

RETRIEVAL = "retrieval chunking reranker embeddings recall context window ragas faithfulness"
AGENTS = "agent trajectory toolcall sandbox replay planner loop budget guardrail harness"


def _item(i: int, text: str) -> Item:
    return Item(
        id=f"it{i}",
        source="devto",
        kind=ItemKind.ARTICLE,
        url=f"https://dev.to/x/{i}",  # type: ignore[arg-type]
        title=f"Post {i}",
        body=f"{text} ```code block ignored``` {text}",
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


CORPUS = [_item(i, RETRIEVAL) for i in range(6)] + [_item(10 + i, AGENTS) for i in range(6)]
GAZ = Gazetteer(
    version="t",
    tools=[ToolEntry(id="ragas", name="Ragas", family="eval")],
    methods=[MethodEntry(id="trajectory-eval", name="trajectory evaluation", origin="discovery")],
)


class TestDiscovery:
    def test_known_vocabulary_and_candidates(self) -> None:
        known = known_vocabulary(GAZ, ["RAG testing"])
        assert {"ragas", "trajectory", "evaluation", "rag", "testing"} <= known
        assert not is_candidate("ragas faithfulness", known)
        assert is_candidate("reranker", known)

    def test_two_themes_separate_and_are_reproducible(self) -> None:
        known = known_vocabulary(GAZ, [])
        result = discover(CORPUS, known, n_topics=2, n_clusters=2, min_df=2)
        clusters = {a.item_id: a.cluster for a in result.assignments}
        assert len({clusters[f"it{i}"] for i in range(6)}) == 1
        assert clusters["it0"] != clusters["it10"]
        assert "reranker" in result.candidate_terms
        assert "ragas" not in result.candidate_terms
        assert "code" not in {t for c in result.clusters for t in c.top_terms}  # fenced code
        again = discover(CORPUS, known, n_topics=2, n_clusters=2, min_df=2)
        assert again.assignments == result.assignments

    def test_too_few_documents(self) -> None:
        with pytest.raises(ValueError, match="documents"):
            discover(CORPUS[:3], set(), n_topics=5, n_clusters=2)

    def test_catalog_origin(self) -> None:
        assert catalog_origin(GAZ) == {"seed": 1, "discovery": 1}


class TestTopicsCommand:
    def test_writes_outputs(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items(CORPUS, "run-t")
        args = ["analyze", "topics", "--run", "run-t", "-p", "protocols/v0_rag_agents_testing.yaml"]
        r = CliRunner().invoke(app, [*args, "--status", "all", "--topics", "2", "--clusters", "2"])
        assert r.exit_code == 0, r.stdout
        out = tmp_path / "reports/run-t/analysis"
        meta = json.loads((out / "discovery.json").read_text())
        assert meta["documents"] == 12 and meta["catalog_origin"]["discovery"] == 0
        assert (out / "topics.csv").exists() and (out / "clusters.csv").exists()
        r = CliRunner().invoke(app, [*args, "--topics", "2", "--clusters", "2"])
        assert r.exit_code == 1  # nothing included yet


class TestCleaning:
    def test_document_drops_markup_code_entities_and_urls(self) -> None:
        from msrkit.discovery import document

        item = _item(0, "")
        item.title = "How to test RAG"
        item.body = (
            "<p>I use &quot;ragas&quot; see https://a.b/c</p>"
            "<pre><code>npm WARN deprecated</code></pre><li>won&#x27;t work &#x2F;x</li>"
        )
        text = document(item)
        assert text == 'How to test RAG I use "ragas" see won\'t work /x'

    def test_short_documents_are_skipped(self) -> None:
        short = [_item(100 + i, "") for i in range(3)]
        for it in short:
            it.body = "tiny"
        result = discover(CORPUS + short, set(), n_topics=2, n_clusters=2, min_df=2)
        assert result.skipped_short == 3
        assert result.documents == len(CORPUS)
        assert {a.item_id for a in result.assignments} == {it.id for it in CORPUS}
