"""GitHub repository enrichment for N3 signals (Phase 2, B1)."""

from __future__ import annotations

import base64
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from typer.testing import CliRunner

from msrkit.adapters.github import GitHubAdapter
from msrkit.cli import app
from msrkit.enrich import GitHubEnricher, _is_test_path, apply_signals, repo_of
from msrkit.models import Item, ItemKind, Provenance
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

TREE = {
    "truncated": False,
    "tree": [
        {"path": "src/app.py", "type": "blob"},
        {"path": "tests/test_rag.py", "type": "blob"},
        {"path": "promptfooconfig.yaml", "type": "blob"},
        {"path": ".github/workflows/eval.yml", "type": "blob"},
        {"path": "pyproject.toml", "type": "blob"},
        {"path": "docs/requirements.txt", "type": "blob"},
    ],
}
WORKFLOW = "jobs:\n  eval:\n    steps:\n      - run: npx promptfoo eval\n"


def _b64(text: str) -> dict[str, Any]:
    return {
        "encoding": "base64",
        "size": len(text),
        "content": base64.b64encode(text.encode()).decode(),
    }


def _fake_get(url: str, params: dict[str, Any] | None = None, headers=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    if url.endswith("/repos/acme/rag"):
        resp.json.return_value = {"default_branch": "main"}
    elif "/git/trees/main" in url:
        resp.json.return_value = TREE
    elif url.endswith("/contents/.github/workflows/eval.yml"):
        resp.json.return_value = _b64(WORKFLOW)
    elif url.endswith("/contents/pyproject.toml"):
        resp.json.return_value = _b64('[project]\ndependencies = ["ragas>=0.1"]\n')
    elif url.endswith("/contributors"):
        resp.headers = {"link": '<https://api.github.com/x?per_page=1&page=7>; rel="last"'}
        resp.json.return_value = [{}]
    elif url.endswith("/commits"):
        resp.json.return_value = [
            {"commit": {"committer": {"date": "2024-03-02T10:00:00Z"}}},
            {"commit": {"committer": {"date": "2024-03-20T10:00:00Z"}}},
            {"commit": {"committer": {"date": "2024-05-01T10:00:00Z"}}},
        ]
    else:
        resp.status_code = 404
    return resp


def _enricher(monkeypatch: pytest.MonkeyPatch) -> GitHubEnricher:
    adapter = GitHubAdapter()
    monkeypatch.setattr(adapter, "_governed_get", _fake_get)
    return GitHubEnricher(
        adapter,
        config_files={"promptfooconfig.yaml"},
        since=date(2024, 1, 1),
        until=date(2024, 12, 31),
    )


def _item(url: str) -> Item:
    return Item(
        id=url[-8:],
        source="github",
        kind=ItemKind.REPO,
        url=url,  # type: ignore[arg-type]
        title="t",
        provenance=Provenance(
            run_id="r",
            query_string="q",
            partition="p",
            adapter="github",
            adapter_version="1",
            fetched_at=datetime.now(UTC),
            response_sha256="",
            raw_ref="",
        ),
    )


class TestEnricher:
    def test_collects_tree_ci_manifests_contributors_and_months(self, monkeypatch) -> None:
        sig = _enricher(monkeypatch).enrich("acme/rag")
        assert sig.error is None
        assert sig.default_branch == "main"
        assert sig.has_tests and sig.test_paths == ["tests/test_rag.py"]
        assert sig.has_ci and "npx promptfoo eval" in sig.workflows[".github/workflows/eval.yml"]
        assert sig.config_paths == ["promptfooconfig.yaml"]
        assert list(sig.manifests) == ["pyproject.toml"]  # only root manifests
        assert sig.contributors == 7
        assert sig.commit_months == ["2024-03", "2024-05"]
        assert sig.requests == len(sig.response_sha256) == 6

    def test_http_error_is_recorded_not_raised(self, monkeypatch) -> None:
        sig = _enricher(monkeypatch).enrich("nope/missing")
        assert sig.error == "repo metadata HTTP 404"

    def test_test_path_detection(self) -> None:
        assert _is_test_path("tests/unit/test_x.py")
        assert _is_test_path("src/foo_test.py")
        assert _is_test_path("web/app.spec.ts")
        assert _is_test_path("evals/golden.jsonl")
        assert not _is_test_path("src/testing_utils.py")

    def test_repo_of_and_apply_signals(self, monkeypatch) -> None:
        sig = _enricher(monkeypatch).enrich("acme/rag")
        code_item = _item("https://github.com/acme/rag/blob/main/tests/test_rag.py")
        other = _item("https://github.com/other/repo")
        assert repo_of(code_item) == "acme/rag"
        assert apply_signals([code_item, other], {"acme/rag": sig}) == 1
        assert code_item.tech.has_ci is True
        assert code_item.tech.contributors == 7
        assert code_item.tech.active_months == 2
        assert other.tech.has_ci is None


class TestEnrichCommand:
    def test_enriches_run_items_and_resumes(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        monkeypatch.setattr(
            GitHubAdapter,
            "_governed_get",
            lambda self, url, params=None, headers=None: _fake_get(url, params),
        )
        ItemStorage(tmp_path).save_items([_item("https://github.com/acme/rag")], "run-e")

        runner = CliRunner()
        result = runner.invoke(app, ["enrich", "--run", "run-e"])
        assert result.exit_code == 0, result.stdout
        assert "1 items updated" in result.stdout
        assert ItemStorage(tmp_path).read_items("run-e")[0].tech.has_ci is True

        result = runner.invoke(app, ["enrich", "--run", "run-e"])
        assert "1 already enriched, 0 to go" in result.stdout
