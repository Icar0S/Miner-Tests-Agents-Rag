# API Notes

This file records divergences between the MSR-Kit specification and
the actual official API documentation, discovered during implementation.

## Format

Each entry follows this template:

```
### [Source] — [Date]
- **Spec says:** ...
- **Docs say:** ...
- **Decision:** ...
- **Link:** ...
```

---

## Entries

### General — 2026-09-03
No divergences found during initial implementation. All endpoints, parameters, and limits were verified against the linked documentation at the time of implementation. Each adapter's `policy.docs_url` points to the official documentation used for verification.

### Dev.to — 2026-09-10
- **Spec says:** Full-text search for research terms across dev.to articles.
- **Docs say:** Dev.to public REST API (`GET /api/articles`) does not offer a free-text search endpoint; it only supports querying by published `tag` or `top`.
- **Decision:** Collect articles by research-relevant tags and perform local keyword filtering (`match_terms`) against article titles, descriptions, and tags. Recorded explicitly in manifest (ADR-004).
- **Link:** https://developers.forem.com/api/v0#tag/articles/operation/getArticles

### Stack Exchange — 2026-09-12
- **Spec says:** Standard HTTP JSON search across Stack Overflow / Software Engineering questions.
- **Docs say:** Stack Exchange API v2.3 strictly requires `Accept-Encoding: gzip` (uncompressed requests trigger errors), enforces dynamic `backoff` directives, and interprets multiple tags as strict `AND` conjunctions.
- **Decision:** Send gzip headers unconditionally, honor platform backoff pauses in Governor, and query by individual relevant sites and search terms without forcing impossible tag conjunctions (ADR-005).
- **Correction (2026-09-28):** The official docs for `/search/advanced` define `tagged` as "a semicolon delimited list of tags, of which **at least one** will be present on all returned questions" (OR semantics), not AND. The adapter's AND assumption is being revised (see `docs/PLANO.md`, step C6).
- **Link:** https://api.stackexchange.com/docs

### Medium via RSS — 2026-09-15
- **Spec says:** Temporal window query for Medium engineering articles (`since` .. `until`).
- **Docs say:** Public Medium feeds (`https://medium.com/feed/tag/...`) provide only the ~10 most recent posts, without pagination or historical date filtering parameters.
- **Decision:** Collect available feed items without attempting unauthorized web scraping. Set `policy.supports_date_filter = False` and record `truncated: true` and `historical_coverage: false` in the run manifest for scientific transparency (ADR-007).
- **Link:** https://medium.com/feed

### Reddit — 2026-09-28
- **Spec says:** OAuth search per subreddit, free tier.
- **Docs say:** Since November 2025 (Responsible Builder Policy), self-service app creation is closed; every new OAuth credential requires manual approval.
- **Decision:** Reddit disabled in the protocol and excluded from Study E2; adapter kept for reuse (ADR-014).
- **Link:** https://support.reddithelp.com/hc/en-us/articles/42728983564564

### Hacker News (Algolia) — 2026-09-28
- **Spec says:** No hard result cap (`max_results_per_query=None`).
- **Docs say:** Algolia's `paginationLimitedTo` caps any query at 1,000 hits regardless of `page`.
- **Decision:** Treat HN like GitHub: estimate with `nbHits` and partition by date window (see `docs/PLANO.md`, step C1).
- **Link:** https://github.com/algolia/hn-search/issues/230
