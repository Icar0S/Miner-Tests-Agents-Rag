"""Tests for opt-in full-text retrieval of linked pages (C4c)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.fulltext import FullTextFetcher, html_to_text
from msrkit.models import Item, ItemKind, Provenance, TermHit
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

ARTICLE = """<html><head><title>Testing RAG</title><style>.x{}</style></head>
<body><nav>Home | Blog</nav><article><h1>Testing RAG</h1>
<p>We use a <b>golden dataset</b> and an LLM judge.</p><script>track()</script>
<p>Second paragraph.</p></article><footer>© 2024</footer></body></html>"""


def _fetcher(routes: dict[str, httpx.Response], sleeps: list[float] | None = None):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(404))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    clock = iter(range(0, 10_000))
    fetcher = FullTextFetcher(
        client=client,
        min_interval_s=5.0,
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
        clock=lambda: float(next(clock)),
    )
    return fetcher, seen


def _html(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})


class TestHtmlToText:
    def test_extracts_visible_text_without_boilerplate(self) -> None:
        title, text = html_to_text(ARTICLE)
        assert title == "Testing RAG"
        assert "golden dataset" in text
        assert "Second paragraph." in text
        for noise in ("track()", "Home | Blog", "© 2024", ".x{}"):
            assert noise not in text


class TestFullTextFetcher:
    def test_fetches_allowed_page(self) -> None:
        fetcher, _ = _fetcher(
            {
                "https://blog.example/robots.txt": httpx.Response(
                    200, text="User-agent: *\nAllow: /"
                ),
                "https://blog.example/post": _html(ARTICLE),
            }
        )
        result = fetcher.fetch("https://blog.example/post")
        assert result.status == "ok"
        assert "golden dataset" in result.text
        assert len(result.text_sha256) == 64

    def test_respects_robots_disallow(self) -> None:
        fetcher, seen = _fetcher(
            {
                "https://blog.example/robots.txt": httpx.Response(
                    200, text="User-agent: *\nDisallow: /private"
                ),
                "https://blog.example/private/post": _html(ARTICLE),
            }
        )
        result = fetcher.fetch("https://blog.example/private/post")
        assert result.status == "robots_disallowed"
        assert "https://blog.example/private/post" not in seen

    def test_missing_robots_allows_but_server_error_blocks(self) -> None:
        fetcher, _ = _fetcher(
            {
                "https://a.example/post": _html(ARTICLE),  # robots.txt -> 404
                "https://b.example/robots.txt": httpx.Response(503),
                "https://b.example/post": _html(ARTICLE),
            }
        )
        assert fetcher.fetch("https://a.example/post").status == "ok"
        assert fetcher.fetch("https://b.example/post").status == "robots_disallowed"

    def test_rejects_non_html(self) -> None:
        fetcher, _ = _fetcher(
            {
                "https://a.example/paper.pdf": httpx.Response(
                    200, content=b"%PDF", headers={"content-type": "application/pdf"}
                )
            }
        )
        assert fetcher.fetch("https://a.example/paper.pdf").status == "not_html"

    def test_waits_between_hits_on_same_host(self) -> None:
        sleeps: list[float] = []
        fetcher, _ = _fetcher({"https://a.example/p": _html(ARTICLE)}, sleeps=sleeps)
        fetcher.fetch("https://a.example/p")  # robots.txt, then page
        # The fake clock advances 1s per reading, so every hit after the first
        # on the host has to wait out the rest of the 5s interval.
        assert sleeps
        assert all(0 < s <= 5.0 for s in sleeps)


def _item(item_id: str, url: str, matched: bool) -> Item:
    return Item(
        id=item_id,
        source="hackernews",
        kind=ItemKind.THREAD,
        url=url,  # type: ignore[arg-type]
        title="t",
        matched_terms=[TermHit(term="RAG testing", field="title", context="c")] if matched else [],
        provenance=Provenance(
            run_id="run-ft",
            query_string="q",
            partition="p",
            adapter="hackernews",
            adapter_version="0.1.0",
            fetched_at=datetime.now(UTC),
            response_sha256="",
            raw_ref="",
        ),
    )


class TestFetchCommand:
    def test_fetches_matched_links_and_resumes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from msrkit import fulltext
        from msrkit.fulltext import FetchResult

        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items(
            [
                _item("a", "https://blog.example/a", matched=True),
                _item("b", "https://blog.example/b", matched=False),
                _item("c", "https://news.ycombinator.com/item?id=1", matched=True),
            ],
            "run-ft",
        )
        fetched: list[str] = []

        def fake_fetch(self: FullTextFetcher, url: str) -> FetchResult:
            fetched.append(url)
            return FetchResult(url, "ok", text="body", text_sha256="x")

        monkeypatch.setattr(fulltext.FullTextFetcher, "fetch", fake_fetch)
        runner = CliRunner()

        result = runner.invoke(app, ["fetch", "--run", "run-ft"])
        assert result.exit_code == 0, result.stdout
        assert fetched == ["https://blog.example/a"]  # unmatched and HN self-post skipped

        out = tmp_path / "fulltext" / "run-ft.jsonl"
        records = [json.loads(line) for line in out.read_text().splitlines()]
        assert records[0]["item_id"] == "a"
        assert records[0]["status"] == "ok"

        result = runner.invoke(app, ["fetch", "--run", "run-ft", "--all"])
        assert result.exit_code == 0, result.stdout
        assert fetched == ["https://blog.example/a", "https://blog.example/b"]  # "a" not refetched

    def test_export_never_includes_fulltext(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items([_item("a", "https://blog.example/a", True)], "run-ft")
        (tmp_path / "fulltext").mkdir()
        (tmp_path / "fulltext" / "run-ft.jsonl").write_text(
            json.dumps({"item_id": "a", "text": "SECRET FULL TEXT"}) + "\n"
        )
        out = tmp_path / "e.jsonl"
        result = CliRunner().invoke(app, ["export", "--run", "run-ft", "-o", str(out)])
        assert result.exit_code == 0
        assert "SECRET FULL TEXT" not in out.read_text()
