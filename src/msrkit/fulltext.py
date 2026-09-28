"""Full-text retrieval of pages linked by collected items (opt-in, local only).

Sources such as Hacker News and RSS return only a link and, at most, a summary.
Extraction (Protocol E2 v2, §12) needs the article text, so `msrkit fetch`
downloads the linked page under these rules:

- robots.txt is honored for the msrkit user agent; if robots.txt cannot be
  read because of a server or network error, the host is treated as disallowed
  (conservative). A 4xx robots.txt means no restrictions, per the standard.
- One request at a time, with a minimum interval between requests to the same host.
- Only HTML responses are processed, and bodies above MAX_BYTES are refused.
- Text is stored under data/fulltext/ for local reading and coding; it is never
  exported (see ADR-019).
"""

from __future__ import annotations

import hashlib
import time
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

USER_AGENT = "msrkit/0.1 (academic research tool)"
MAX_BYTES = 2_000_000
_SKIP_TAGS = {"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg"}
_BLOCK_TAGS = {"p", "div", "section", "article", "li", "br", "h1", "h2", "h3", "h4", "pre", "tr"}


class _TextExtractor(HTMLParser):
    """Visible text of an HTML page, one block per line, without boilerplate tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        lines = (" ".join(line.split()) for line in "".join(self._parts).split("\n"))
        return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> tuple[str, str]:
    """Return (title, visible text) of an HTML document."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return " ".join(parser.title.split()), parser.text()


@dataclass
class FetchResult:
    """Outcome of one full-text fetch."""

    url: str
    status: str  # ok | robots_disallowed | http_error | not_html | too_large | error
    reason: str = ""
    title: str = ""
    text: str = ""
    text_sha256: str = ""
    fetched_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class FullTextFetcher:
    """Polite, robots.txt-aware fetcher of linked article pages."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        min_interval_s: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        self._min_interval = min_interval_s
        self._sleep = sleep
        self._clock = clock
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}

    def close(self) -> None:
        self._client.close()

    def _wait_for_host(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            remaining = self._min_interval - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last_hit[host] = self._clock()

    def _robots_for(self, scheme: str, host: str) -> urllib.robotparser.RobotFileParser | None:
        """Parsed robots.txt, an allow-all parser for 4xx, or None when unreadable."""
        if host in self._robots:
            return self._robots[host]
        robots = urllib.robotparser.RobotFileParser()
        parser: urllib.robotparser.RobotFileParser | None = robots
        try:
            self._wait_for_host(host)
            resp = self._client.get(f"{scheme}://{host}/robots.txt")
            if resp.status_code >= 500:
                parser = None
            elif resp.status_code >= 400:
                robots.parse([])  # no robots.txt: everything allowed
            else:
                robots.parse(resp.text.splitlines())
        except httpx.HTTPError:
            parser = None
        self._robots[host] = parser
        return parser

    def fetch(self, url: str) -> FetchResult:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return FetchResult(url, "error", "unsupported URL")
        host = parsed.hostname.lower()

        robots = self._robots_for(parsed.scheme, host)
        if robots is None:
            return FetchResult(url, "robots_disallowed", "robots.txt unreadable")
        if not robots.can_fetch(USER_AGENT, url):
            return FetchResult(url, "robots_disallowed", "disallowed by robots.txt")

        try:
            self._wait_for_host(host)
            resp = self._client.get(url)
        except httpx.HTTPError as e:
            return FetchResult(url, "error", type(e).__name__)

        if resp.status_code != 200:
            return FetchResult(url, "http_error", str(resp.status_code))
        if "html" not in resp.headers.get("content-type", "").lower():
            return FetchResult(url, "not_html", resp.headers.get("content-type", ""))
        if len(resp.content) > MAX_BYTES:
            return FetchResult(url, "too_large", str(len(resp.content)))

        title, text = html_to_text(resp.text)
        return FetchResult(
            url,
            "ok",
            title=title,
            text=text,
            text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
