"""Unit tests for rate limit resilience, clock skew buffer, and defensive partitioning."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import httpx

from msrkit.adapters.base import BaseAdapter
from msrkit.governor import Governor
from msrkit.models import (
    Availability,
    AvailabilityStatus,
    Item,
    ItemKind,
    Query,
    RateLimit,
    RawItem,
    RedistributionPolicy,
    SourcePolicy,
)
from msrkit.partition import partition


class DummyClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.current = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += seconds


class ConcreteTestAdapter(BaseAdapter):
    name = "test_source"
    version = "0.1.0"
    policy = SourcePolicy(
        requires_auth=False,
        auth_env_vars=[],
        rate_limit=RateLimit(requests=10, per_seconds=60, burst=5),
        max_results_per_query=1000,
        max_page_size=100,
        max_pages=10,
        supports_full_text_search=True,
        supports_date_filter=True,
        redistribution=RedistributionPolicy.METADATA_ONLY,
        tos_url="",
        docs_url="",
        notes="",
        has_official_api=True,
        official_api_name="Test API",
    )

    def available(self) -> Availability:
        return Availability(status=AvailabilityStatus.OK, reason="Test")

    def estimate(self, q: Query) -> int | None:
        return 100

    def search(self, q: Query):
        return iter([])

    def normalize(self, raw: RawItem, terms: list[str] | None = None) -> Item:
        return Item(
            id="1", source=self.name, kind=ItemKind.ARTICLE, url="https://example.com", title="Test"
        )


class TestRateResilience:
    def test_governed_get_retries_on_rate_limit_with_clock_buffer(self) -> None:
        clock = DummyClock()
        gov = Governor("test_source", ConcreteTestAdapter.policy.rate_limit, clock=clock)
        adapter = ConcreteTestAdapter(governor=gov)

        # Responses: first 403 with x-ratelimit-reset, second 200 OK
        resp_403 = httpx.Response(
            status_code=403,
            headers={
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": "1030.0",  # 30s in the future from clock.current=1000.0
            },
            content=b'{"message": "rate limit exceeded"}',
            request=httpx.Request("GET", "https://api.example.com/test"),
        )
        resp_200 = httpx.Response(
            status_code=200,
            content=b'{"items": []}',
            request=httpx.Request("GET", "https://api.example.com/test"),
        )

        with patch.object(adapter, "_request_with_retry", side_effect=[resp_403, resp_200]):
            res = adapter._governed_get("https://api.example.com/test")

        assert res.status_code == 200
        # Clock must have slept 30s (reset_at - now) + 1.5s (clock buffer) = 31.5s
        assert len(clock.sleeps) >= 2
        assert 30.0 in clock.sleeps
        assert 1.5 in clock.sleeps

    def test_governed_get_defensive_backoff_on_secondary_rate_limit(self) -> None:
        clock = DummyClock()
        gov = Governor("test_source", ConcreteTestAdapter.policy.rate_limit, clock=clock)
        adapter = ConcreteTestAdapter(governor=gov)

        # 403 without reset headers (secondary rate limit), then 200
        resp_secondary_403 = httpx.Response(
            status_code=403,
            headers={},  # No remaining or reset headers
            content=b'{"message": "You have exceeded a secondary rate limit."}',
            request=httpx.Request("GET", "https://api.example.com/test"),
        )
        resp_200 = httpx.Response(
            status_code=200,
            content=b'{"items": []}',
            request=httpx.Request("GET", "https://api.example.com/test"),
        )

        with patch.object(
            adapter, "_request_with_retry", side_effect=[resp_secondary_403, resp_200]
        ):
            res = adapter._governed_get("https://api.example.com/test")

        assert res.status_code == 200
        # Should have applied fallback backoff (5.0s on attempt 0)
        assert 5.0 in clock.sleeps

    def test_governed_get_does_not_retry_bad_credentials(self) -> None:
        clock = DummyClock()
        gov = Governor("test_source", ConcreteTestAdapter.policy.rate_limit, clock=clock)
        adapter = ConcreteTestAdapter(governor=gov)

        resp_bad_cred = httpx.Response(
            status_code=403,
            content=b'{"message": "Bad credentials"}',
            request=httpx.Request("GET", "https://api.example.com/test"),
        )

        with patch.object(adapter, "_request_with_retry", return_value=resp_bad_cred) as mock_req:
            res = adapter._governed_get("https://api.example.com/test")

        assert res.status_code == 403
        # Must return immediately without sleeping or retrying
        assert mock_req.call_count == 1
        assert len(clock.sleeps) == 0


class TestDefensivePartitioning:
    def test_partition_splits_long_window_when_estimate_is_none(self) -> None:
        # Multi-year query (Jan 2023 to Aug 2026 = ~1338 days)
        q = Query(
            source="github",
            terms=["agent evaluation"],
            since=date(2023, 1, 1),
            until=date(2026, 8, 31),
        )

        # estimate_fn always returns None (simulating API estimate failure)
        def estimate_none(query: Query) -> int | None:
            return None

        partitions = partition(q, max_results=1000, estimate_fn=estimate_none)

        # Should NOT return 1 partition; must have split into multiple date sub-windows defensively
        assert len(partitions) >= 4
        # All sub-windows must cover the original window without gaps
        since_dates = [p[0].since for p in partitions]
        until_dates = [p[0].until for p in partitions]
        assert min(since_dates) == date(2023, 1, 1)
        assert max(until_dates) == date(2026, 8, 31)

    def test_partition_does_not_split_short_window_when_estimate_is_none(self) -> None:
        # Short query (15 days)
        q = Query(
            source="github",
            terms=["agent evaluation"],
            since=date(2024, 1, 1),
            until=date(2024, 1, 16),
        )

        def estimate_none(query: Query) -> int | None:
            return None

        partitions = partition(q, max_results=1000, estimate_fn=estimate_none)
        # For short windows <= 180 days, it is safe to keep 1 partition
        assert len(partitions) == 1
        assert partitions[0][0].since == date(2024, 1, 1)
        assert partitions[0][0].until == date(2024, 1, 16)
