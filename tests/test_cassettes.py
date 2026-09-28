"""Contract tests against recorded API responses (Phase 2, V3; Protocol §10.2).

Each case replays a vcrpy cassette from tests/cassettes/ with record mode
"none": any request the adapter makes that is not in the cassette fails the
test, so a change in how an adapter talks to its API is caught offline. The
contract checks that every recorded result normalizes into a valid Item, that
the payload still carries the keys the adapter relies on (API drift), and that
normalization is deterministic.

Recording (needs network and, for GitHub, a token):

    MSRKIT_RECORD=1 pytest tests/test_cassettes.py -k <case>

Delete a cassette to re-record it (record mode "once" never overwrites).

A case whose cassette is absent is skipped with that instruction. Secrets are
never written: auth headers and key/token query parameters are filtered and
cookies dropped (see VCR_CONFIG); `test_no_secrets_in_cassettes` checks it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import vcr

from msrkit.models import Item, Query
from msrkit.registry import all_adapters, discover_adapters

CASSETTES = Path(__file__).parent / "cassettes"
RECORD = os.environ.get("MSRKIT_RECORD") == "1"
HERE = Path(__file__).name


def _scrub_response(response: dict[str, Any]) -> dict[str, Any]:
    for name in list(response.get("headers", {})):
        if name.lower() in {"set-cookie", "x-github-request-id", "x-oauth-scopes"}:
            del response["headers"][name]
    return response


VCR_CONFIG: dict[str, Any] = {
    "cassette_library_dir": str(CASSETTES),
    "record_mode": "once" if RECORD else "none",
    "filter_headers": ["authorization", "x-api-key", "cookie", "user-agent"],
    "filter_query_parameters": ["key", "access_token", "client_id", "client_secret", "token"],
    "before_record_response": _scrub_response,
    "decode_compressed_response": True,
    "match_on": ["method", "scheme", "host", "path", "query"],
}
recorder = vcr.VCR(**VCR_CONFIG)


@dataclass
class SearchCase:
    adapter: str
    cassette: str
    query: Query
    raw_keys: list[str] = field(default_factory=list)  # payload keys normalize relies on
    min_items: int = 1


WINDOW = {"since": date(2025, 1, 1), "until": date(2025, 6, 30)}
SEARCH_CASES = [
    SearchCase(
        "github",
        "github/search_repos.yaml",
        Query(source="github", kind="repo", terms=["ragas"], limit=5, **WINDOW),
        ["full_name", "html_url", "created_at"],
    ),
    SearchCase(
        "stackexchange",
        "stackexchange/search.yaml",
        Query(source="stackexchange", terms=["rag evaluation"], limit=5, **WINDOW),
        ["question_id", "link", "title", "creation_date"],
    ),
    SearchCase(
        "hackernews",
        "hackernews/search.yaml",
        Query(source="hackernews", terms=["rag evaluation"], limit=5, **WINDOW),
        ["objectID", "created_at"],
    ),
    SearchCase(
        "devto",
        "devto/search.yaml",
        Query(source="devto", terms=["rag"], limit=5, **WINDOW),
        ["id", "url", "published_at"],
    ),
    SearchCase(
        "huggingface",
        "huggingface/search.yaml",
        Query(source="huggingface", kind="model", terms=["rag"], limit=5, **WINDOW),
        ["id"],
    ),
]


def _use(cassette: str) -> Any:
    if not RECORD and not (CASSETTES / cassette).exists():
        pytest.skip(f"cassette {cassette} not recorded (MSRKIT_RECORD=1 pytest {HERE})")
    return recorder.use_cassette(cassette)


@pytest.fixture(scope="module", autouse=True)
def _discover() -> None:
    discover_adapters()


@pytest.mark.parametrize("case", SEARCH_CASES, ids=lambda c: c.adapter)
def test_search_contract(case: SearchCase) -> None:
    adapter = all_adapters()[case.adapter]()
    with _use(case.cassette):
        raws = list(adapter.search(case.query))
    adapter.close()

    assert len(raws) >= case.min_items, "recorded search returned no results"
    for raw in raws:
        missing = [k for k in case.raw_keys if k not in raw.payload]
        assert not missing, f"API drift in {case.adapter}: payload lacks {missing}"
        first = adapter.normalize(raw, terms=case.query.terms)
        again = adapter.normalize(raw, terms=case.query.terms)
        assert isinstance(first, Item)
        assert first.source == case.adapter
        assert str(first.url).startswith("http")
        assert first.id == again.id == Item.make_id(case.adapter, raw.native_id)


def test_enrich_contract() -> None:
    """Repository enrichment of this project's own repository, recorded."""
    from msrkit.adapters.github import GitHubAdapter
    from msrkit.enrich import GitHubEnricher

    adapter = GitHubAdapter()
    enricher = GitHubEnricher(
        adapter,
        config_files={"promptfooconfig.yaml"},
        since=date(2026, 9, 1),
        until=date(2026, 9, 27),
    )
    with _use("github/enrich_self.yaml"):
        sig = enricher.enrich("Icar0S/Miner-Tests-Agents-Rag")
    adapter.close()

    assert sig.error is None, sig.error
    assert sig.default_branch
    assert sig.has_tests and any(p.startswith("tests/") for p in sig.test_paths)
    assert sig.has_ci and ".github/workflows/ci.yml" in sig.workflows
    assert "pyproject.toml" in sig.manifests
    assert (sig.contributors or 0) >= 1
    assert sig.commit_months and all(re.fullmatch(r"\d{4}-\d{2}", m) for m in sig.commit_months)
    assert len(sig.response_sha256) == sig.requests


SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)authorization:\s*\n\s*-\s*(bearer|token)\s"),
    re.compile(r"[?&](key|access_token|client_secret)=(?!DUMMY)[^&\s']+"),
]


def test_no_secrets_in_cassettes() -> None:
    for path in CASSETTES.rglob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        for pattern in SECRET_PATTERNS:
            assert not pattern.search(text), f"possible secret in {path}: {pattern.pattern}"
