# Architecture & Design Decisions (ADRs)

This document records architectural, design, and technical decisions made during the implementation of MSR-Kit v0. As specified in §20.4, whenever ambiguity arises, the **most conservative option** is chosen: the one that makes fewer requests, retains less data, and fails earlier.

---

## ADR-001: Pre-Emptive Rate Limiting via Token Bucket

- **Context:** APIs enforce rate limits through headers and status codes (429/403). Relying only on reactive backoff (waiting until a 429 occurs) can lead to temporary IP bans, account flags, and quota exhaustion.
- **Decision:** Implement an in-memory Token Bucket governor (`Governor`) configured directly from `policy.rate_limit`. The governor blocks *before* making the HTTP request (`acquire()`). In addition, response headers (`Retry-After`, `x-ratelimit-remaining`, `x-ratelimit-reset`, and Stack Exchange `backoff`) dynamically adjust bucket capacity and sleep intervals.
- **Conservative Principle:** Request pacing is proactive rather than reactive, minimizing API stress and avoiding rate-limit violations.

---

## ADR-002: Disk Persistence of Daily Rate Quotas

- **Context:** A collection process may be interrupted and resumed across different processes on the same day. Without persistent state, daily quota counters would reset to zero, risking quota breaches.
- **Decision:** The `Governor` persists its request count and timestamp to `data/.governor_state.json`. When instantiated, it restores today's count if within the same UTC day, or rolls over if the date has changed.
- **Conservative Principle:** Resuming a run never exceeds daily quotas declared by providers (e.g. Stack Exchange 300 req/day without key, 10,000 with key).

---

## ADR-003: GitHub Search Partitioning under 1,000 Results Cap

- **Context:** GitHub's Search API enforces an absolute limit of 1,000 results per query (10 pages of 100 items). Queries returning >1,000 items silently truncate items beyond 1,000.
- **Decision:** Implement a recursive time-window partitioning algorithm in `partition.py`. If `estimate()` indicates `total_count > 1000`, divide the date window `[since, until]` in half until each partition fits within 1,000 results. If a 1-day interval still exceeds 1,000, split across secondary axes (`language:`, then `stars:a..b`). If no further subdivision is possible, mark `truncated=True` and log estimated loss.
- **Conservative Principle:** Prioritize precision and completeness over speed; detect and explicitly flag sample truncation in the run manifest.

---

## ADR-004: dev.to Tag-Based Retrieval with Local Keyword Matching

- **Context:** dev.to's public API does not offer full-text search endpoints; articles can only be retrieved by `tag`.
- **Decision:** Mark `policy.supports_full_text_search = False`. The adapter queries by configured tags and applies local keyword filtering (`match_terms`) against article titles and descriptions before yielding raw items. The manifest explicitly records that selection was performed locally.
- **Conservative Principle:** Does not invent unapproved query parameters; collects by published tag boundaries and discards non-matching items early.

---

## ADR-005: Stack Exchange Gzip Encoding and 1 req/min Pacing

- **Context:** Stack Exchange API v2.3 strictly requires `Accept-Encoding: gzip` (returning uncompressed responses is not supported) and enforces strict backoff rules. Rapid requests can result in automatic IP throttling.
- **Decision:** The adapter sends `Accept-Encoding: gzip` on every request and sets default rate limiting to 1 request per 60 seconds. Responses are inspected for the `backoff` field, which immediately pauses the governor if present.
- **Conservative Principle:** Respects SE's caching architecture and honors platform backoff directives unconditionally.

---

## ADR-006: Reddit Strict OAuth2 Authentication

- **Context:** Reddit disallows unauthenticated scraping and anonymous search API access. An explicit descriptive `User-Agent` is mandatory per platform guidelines.
- **Decision:** Reddit adapter requires `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, and `REDDIT_USER_AGENT`. In their absence, `available()` returns `UNSUPPORTED`. No fallbacks to unauthenticated web scraping are permitted (§2.1).
- **Conservative Principle:** Fails early during `validate` and `plan` when credentials are not configured, preventing unexpected execution failures.

---

## ADR-007: Medium via RSS and Truncation Transparency

- **Context:** Medium feeds (`/feed/tag/...`) return only the ~10 most recent posts. Historical temporal queries are impossible via RSS.
- **Decision:** The RSS adapter collects only current feeds, marks `policy.supports_date_filter = False`, and sets `truncated: true` and `historical_coverage: false` in the run manifest for RSS sources.
- **Conservative Principle:** Never attempts web scraping or unofficial endpoints to obtain past Medium stories. Document the sampling bias transparently in research artifacts.

---

## ADR-008: Permanent Exclusion of LinkedIn

- **Context:** LinkedIn does not provide a public search API for user posts/articles. Content research on third-party user data is not an approved use case, and scraping violates the User Agreement.
- **Decision:** Implement `LinkedInAdapter` permanently returning `AvailabilityStatus.UNSUPPORTED` with a detailed explanation. Its `search()` method unconditionally raises `SourceUnsupportedError`.
- **Conservative Principle:** Complete refusal to circumvent platform constraints, maintaining legal, ethical, and academic research integrity (§2.2).

---

## ADR-009: Gated Status for Discord and X / Twitter

- **Context:** Discord bot access to message content requires privileged intents, guild installation, and administrator consent. X (Twitter) search API requires paid tiers that vary frequently in pricing and limits.
- **Decision:** In v0, both adapters remain gated:
  - Discord returns `UNSUPPORTED` unless `DISCORD_BOT_TOKEN` and `DISCORD_GUILD_IDS` are set, and documentation notes the requirement for prior ethical review.
  - X/Twitter returns `UNSUPPORTED` without `X_BEARER_TOKEN` and `DEGRADED` with a token, instructing researchers to verify their specific tier's historical search window.
- **Conservative Principle:** Avoid hardcoding assumed tier quotas or harvesting chat messages without administrative and ethical clearance.

---

## ADR-010: Redistribution Policy Enforcement on Export

- **Context:** Different sources permit different redistribution terms:
  - Stack Exchange content is licensed under CC BY-SA (`full_text_with_attribution`).
  - GitHub repositories, Dev.to articles, Reddit posts, Hugging Face cards have varied third-party copyright licenses (`metadata_only`).
- **Decision:**
  - When `--include-body` is passed to `msrkit export`, the CLI inspects all sources present in the items. If any source has `policy.redistribution == "metadata_only"`, export terminates immediately with an error (exit code 1) before creating files.
  - When `--include-body` is omitted (default), body content is stripped from JSONL and CSV exports, and omitted from DuckDB tables and embedded JSON.
- **Conservative Principle:** Protects researchers from unintentional redistribution of copyrighted material.

---

## ADR-011: Post-Normalization Term Matching & Context Windows

- **Context:** Source adapters normalize raw JSON responses into canonical `Item` instances. However, raw responses do not carry the research protocol's search terms.
- **Decision:**
  - `adapter.normalize(raw, terms=...)` accepts optional terms.
  - Both `run` and `normalize` commands enforce post-normalization term matching using `match_terms()` against item title, body, tags, and path.
  - Matches use case-insensitive exact phrase matching with word boundaries, generating a ±40 token context window.
  - Items with zero matches are preserved (not dropped) to record retrieval precision/noise statistics for empirical evaluation.
- **Conservative Principle:** Deterministic keyword detection without non-reproducible ML heuristics or opaque classification models.

---

## ADR-012: Deduplication Strategy (Canonical URL & Content Hash)

- **Context:** Cross-platform literature mining often produces duplicates (e.g. shared articles, mirror posts, cross-posted questions).
- **Decision:** Two-pass deterministic deduplication:
  1. **URL canonicalization:** lowercase scheme and host, stripping tracking query parameters (`utm_*`, `ref`, `source`, `fbclid`, etc.), stripping trailing slashes on non-root paths, and sorting remaining query parameters.
  2. **Content hashing (with strict entity protection):** SHA-256 over normalized `title + " " + body` (collapsed whitespace).
     - **Safety rule 1 (Mandatory non-empty body):** Content hashing is only executed when `body` is present and non-empty (`has_body = bool(item.body and item.body.strip())`).
     - **Safety rule 2 (Code artifact exclusion):** Code files (`ItemKind.CODE`) are strictly excluded from content hashing pass (`item.kind != ItemKind.CODE`).
     - **Rationale:** Code search items (which share common filenames like `test_rag.py`, `eval.py`) and title-only link posts rely exclusively on canonical URL deduplication. This completely prevents false-positive mergers between distinct code files across different repositories or distinct articles sharing generic headlines.
  - MinHash/LSH near-duplicate detection is deferred to v1.
- **Conservative Principle:** Deterministic, reproducible, and verifiable deduplication with zero false-positive entity mergers.

---

## ADR-013: Windows Console UTF-8 Stream Reconfiguration

- **Context:** On Windows PowerShell and Command Prompt environments using legacy codepages (e.g. `cp1252`), Rich terminal formatting using Unicode symbols (such as checkmarks `✓` and crosses `✗`) triggers `UnicodeEncodeError`.
- **Decision:** In `msrkit/cli/__init__.py`, `sys.stdout` and `sys.stderr` are reconfigured to UTF-8 with `errors="replace"` if running on `win32`.
- **Conservative Principle:** Ensures rock-solid CLI execution on any developer workstation without requiring external shell adjustments.

---

## ADR-014: Reddit Excluded from Study E2 (Responsible Builder Policy)

- **Context:** Since November 2025, Reddit closed self-service creation of OAuth apps. Under the Responsible Builder Policy every new credential requires manual prior approval, with no deadline or guarantee; researchers are routed to the Reddit for Researchers program. Pushshift remains restricted to moderators.
- **Decision:** Reddit is disabled in `protocols/v0_rag_agents_testing.yaml` and excluded from Study E2 (Protocol v2, §6.1). The adapter is kept unchanged so it can be reactivated if an approved credential is obtained before collection, and for reuse in other studies. Third-party dumps (Academic Torrents) are not used because their terms status is uncertain.
- **Conservative Principle:** Never depend on an access path that the platform does not grant through its official process.

---

## ADR-015: Source Feasibility Tracked Against Protocol E2 v2

- **Context:** Access conditions change faster than the code (X moved to pay-per-use, Google Custom Search closes on 2027-01-01, Bing Web Search was retired on 2025-08-11).
- **Decision:** `docs/sources.md` holds the feasibility table (viable / conditional / infeasible) with the verification date and official links. It must be re-verified before each collection run. Execution conditions required by the protocol are tracked in `docs/PLANO.md`.
- **Conservative Principle:** A source's status is a dated claim backed by official documentation, not an assumption baked into code.

---

## ADR-016: Flexible Term Matching and Audited Local Discards (supersedes the exact-phrase rule of ADR-011)

- **Context:** Exact-phrase matching dropped relevant items in sources filtered locally (dev.to, RSS): "Evaluating RAG pipelines" did not match "evaluate RAG", and "LLM-as-a-judge" did not match "LLM as a judge". Discards were silent, breaking PRISMA auditability (Protocol E2 v2, §6.5 and §11).
- **Decision:** `match_terms` defaults to a deterministic `flexible` mode: each word of a term tolerates one inflectional suffix (light stem + `\w*`) and words may be separated by spaces, hyphens, underscores or slashes. Words shorter than four characters (acronyms such as RAG, LLM) stay exact, allowing only a plural "s". Terms with symbols (C++, .NET) keep exact boundaries. `mode="exact"` remains available. Items dropped by a local filter are written to `data/runs/<run_id>/discarded.jsonl` with the reason, and counted in the manifest (`discarded`).
- **Conservative Principle:** Recall losses are made visible and auditable; matching remains rule-based and reproducible. Word order is still significant ("testing the agent" does not match "agent testing").

---

## ADR-017: Stack Exchange `tagged` Uses OR Semantics (supersedes the AND assumption)

- **Context:** The adapter assumed that `;` in `tagged` meant AND and therefore never sent tags together with search terms. The official documentation of `/search/advanced` states the opposite: `tagged` is "a semicolon delimited list of tags, of which at least one will be present on all returned questions".
- **Decision:** Each (site, term) request carries the full tag list joined by `;` (OR), as in Protocol E2 v2 §7.3. The `tagged_mode: and` option was removed, since the endpoint does not offer AND. Sites follow the protocol: stackoverflow, softwareengineering, sqa, datascience, ai.
- **Verification:** the pilot compares, for one term, the result count with the tag list against the union of single-tag requests; a mismatch reopens this ADR.

---

## ADR-018: Per-Language Lexicons and Language Stratum

- **Context:** Protocol E2 accepts English and Portuguese items (criterion I4) and reports a Portuguese exploratory stratum, but the protocol had only English terms and the `languages` field was unused.
- **Decision:** `terms_by_language` adds lexicons per language next to the base `terms` (English); every key must be declared in `languages`. Queries search all lexicons; each `TermHit` carries the `lang` of its lexicon, and the CSV export adds `matched_languages`. Flexible matching folds accents (positions preserved, so context windows keep the original text) and strips Portuguese `-ção/-ções` suffixes. `pt.stackoverflow` is added to the Stack Exchange sites.
- **Conservative Principle:** The stratum is derived from which lexicon matched — a reproducible rule — rather than from automatic language detection.

---

## ADR-019: Opt-in Full-Text Retrieval of Linked Pages (`msrkit fetch`)

- **Context:** Hacker News and RSS items carry only a link and, at most, a summary, but extraction (Protocol E2 v2, §12) codes oracle types and failure modes from the article text. The linked pages are ordinary public web pages, not APIs.
- **Decision:** A separate, opt-in command `msrkit fetch` downloads the linked page for items of a run (by default only items with matched terms, from `hackernews` and `rss`). It honors robots.txt for the msrkit user agent, treats an unreadable robots.txt (5xx or network error) as disallowed, keeps a minimum per-host interval (5 s by default), processes only HTML up to 2 MB, and extracts visible text with the standard library. Results, including refusals, go to `data/fulltext/<run_id>.jsonl`; the command resumes without refetching. **Full text is never exported**: `export` does not read this directory.
- **Conservative Principle:** Collection through official APIs is unchanged; reading linked pages is explicit, rate-limited, robots-aware, local-only and auditable (every refusal is recorded with its reason).

---

## ADR-020: Literal Per-Source Queries (`queries:`)

- **Context:** Protocol E2 v2 §7.3 specifies source-specific searches that a global term list cannot express: GitHub code search for imports and config files (N2), workflow files (N3), issues of anchor tools.
- **Decision:** Each source in `protocol.yaml` may declare `queries:` — literal strings in the source's own search syntax, with optional `kind`, `label` and target `evidence` level — run in addition to the terms (or alone, with `use_terms: false`). Adapters declare `supports_raw_queries`; `validate` rejects `queries:` on sources without a search syntax (dev.to, RSS). GitHub keeps the literal string untouched except for the `created:` window used by partitioning (not added to code search or when the query sets its own `created:`).
- **Conservative Principle:** The exact string sent to the API is the one written in the protocol, so it is recorded verbatim in the manifest and reproducible.

---

## ADR-021: Versioned Gazetteer

- **Context:** Protocol E2 v2 anchors search and extraction in a gazetteer of known tools and methods (§7.2), with aliases and disambiguation rules (§12.2), kept outside the protocol document because it changes faster.
- **Decision:** `protocols/gazetteer.yaml`, named by `gazetteer:` in the protocol and versioned with it. Tools carry canonical id, aliases, family (Annex A), target system (`rag`/`agente`), and the structural signals used for N2/N3 (Python imports, dependency names, config files, CLI commands) plus their own repositories. Methods carry aliases and their usual rung on the oracle ladder. Ids and aliases must be unique across entries. Tools marked `ambiguous` only count with lexicon context or a structural signal; frameworks of the system under test are listed with `anchor: false`. Query templates `{anchor}` and `{repo}` expand over anchor tools (optionally by family).
- **Note:** repositories and package names in the seed must be checked during the pilot; the open discovery pass (§7.6) exists because any gazetteer biases what is found.

---

## ADR-022: Concept Groups and System Label

- **Context:** RQ5 compares RAG and agentic systems, and §3.1 lets a hybrid item carry both labels. Items carried no system label.
- **Decision:** The protocol declares `concepts:` — named lexicons (here `rag`, `agente`, `teste`). After normalization every item gets `concepts`, the list of groups whose lexicon matches (flexible matching, ADR-016), exported as a CSV column. The E2 system label is `concepts ∩ {rag, agente}`. Optional `concept_queries` adds query terms built as the product of concept lexicons (e.g. system × testing). The mechanism is domain-agnostic: another study defines other groups.
- **Conservative Principle:** The label is an automatic pre-classification by lexicon, reproducible and auditable; manual coding (Annex A) remains the reference.

---

## ADR-023: Evidence-Level Extraction Rules

- **Context:** §3.4 separates mention (N1), declared use (N2) and sustained adoption (N3), and every reported count must state its level.
- **Decision:** `msrkit extract` applies the gazetteer. N1: tool or method name in title, body or tags (tool names in exact mode, methods in flexible mode). N2: a code search hit whose literal query is an import of the tool, a tool config file, or a tool package in a root dependency manifest. N3: the tool invoked in a CI workflow **and** the repository has commits in ≥2 distinct months of the window **and** ≥2 contributors; a CI invocation without both thresholds counts as N2. Methods are N1 only. Summaries count each unit (a repository, or an item outside GitHub) once per entry at its highest level, so N1/N2/N3 counts are disjoint. Every detection keeps its signal, location and an evidence excerpt of at most 300 characters.
- **Limit:** a code search hit is trusted to contain its query string; precision is measured on a stratified sample (§12.3, step V2).

---

## ADR-024: Screening Sheets with Per-Coder Decisions

- **Context:** Screening (§8, §11) is manual and must be auditable per item, with independent double coding (§15).
- **Decision:** Eligibility criteria live in the protocol (`screening.inclusion`/`exclusion`, `batch_size`), so the workflow is study-agnostic. `msrkit screen export` writes a CSV sheet (`;`, UTF-8 BOM) in batches with one column per criterion, `decision`, `reason`, `coder`, `notes`, skipping items the coder already decided. `msrkit screen import` validates the whole sheet before recording anything (unknown item, invalid decision, missing coder, *include* with an exclusion criterion marked, *exclude* without criterion or reason) and appends one record per (item, coder) with the sheet's SHA-256 to `data/screening/<run>/decisions.jsonl`. The latest decision per coder wins; the final decision per item is the agreed one, or `uncertain` when coders disagree (to be resolved by a third coder).

---

## ADR-025: Per-Partition Checkpoint and Exact Resume

- **Context:** The manifest was written only when a run finished, so an interrupted run could not be resumed at all, and resume skipped whole sources. RNF3 requires resuming without loss or duplication.
- **Decision:** Each partition has a stable key (`query_key`: source, kind, literal flag, label, terms, window, extra — not position or limit), used as the raw-storage partition name and item `provenance.partition`. The partition plan is frozen in the manifest (`SourceManifestEntry.planned`) before collecting, and the manifest is saved after every partition with `key` and `completed`. `--resume` reuses the frozen plan (no re-estimation), skips completed partitions, and first deletes the raw file, items and manifest entries of incomplete ones, which are then collected from scratch.

---

## ADR-026: Near-Duplicate Pass (MinHash/LSH)

- **Context:** Cross-posted questions, mirrored READMEs and reposted articles survive URL and content-hash deduplication because a single character differs, inflating counts (RF5, §11).
- **Decision:** `msrkit dedupe` runs a third pass after URL and exact-content passes: MinHash signatures (128 permutations, word 5-shingles, blake2b with a fixed seed) and LSH (32 bands) propose candidate pairs, kept as duplicates when the estimated Jaccard similarity is ≥ `--near-threshold` (default 0.85; `0` disables). Texts with fewer than 30 tokens are skipped, since short titles collide by chance. The earliest-collected item of a group is kept. Counts per reason (url, content, near) and every removed pair with its similarity go to `dedupe_report.json`, which feeds the PRISMA flow (step T6).
- **Calibration:** the threshold is a pilot parameter; the report's similarity values allow checking borderline pairs manually.

---

## ADR-027: Relevance-Ordered Screening in Fixed Batches

- **Context:** §11 asks screening to start with the most informative items and proceed in fixed batches, so pilot calibration and double coding compare the same sets.
- **Decision:** `msrkit screen export` ranks the whole run by an additive, explainable score: 1 point per distinct matched term (max 5), +2 for a system concept (`rag`/`agente`), +2 for the testing concept, +1/+3/+5 for the best evidence level detected by `msrkit extract` (N1/N2/N3), +1 each for `has_tests` and `has_ci`. Ties break by item id. Batches (`screening.batch_size`) are cut on the full ranking before filtering pending items, so an item keeps its batch across coders and re-exports; `--batch` exports selected batches and `--order collected` restores collection order. The sheet shows `rank`, `score` and `evidence`.
- **Conservative Principle:** ranking orders the work only; no item is excluded by score.

---

## ADR-028: Declarative Extraction Form with Separate Suggestions

- **Context:** Annex A defines the per-item extraction form and §9 the gray-literature quality checklist. Both are coded manually, double-coded on a sample (§15), and must use the same identifiers as extraction and analysis.
- **Decision:** The protocol declares the form in `coding.fields` (types `enum`, `multi`, `text`, `bool`; `required`; `max_length`) and the failure-mode catalog in `coding.failure_modes` (FP1–FP13, AF1–AF12). A field's allowed values may come from the gazetteer (`vocabulary: tools|methods`) or the catalog (`failure_modes`). `msrkit coding export` writes the included items (by final screening decision) with read-only `auto_*` columns — system label, best evidence level, detected tools and methods, evidence excerpt, engagement and maintenance signals. `msrkit coding import` validates the whole sheet (unknown value, length, required field, yes/no) before appending one record per (item, coder) to `data/coding/<run>/codings.jsonl`. Values are matched case- and accent-insensitively and stored in their canonical form.
- **Conservative Principle:** suggestions are copied into the form only with `--prefill`. The default keeps coders independent, so agreement (step T5) measures the coders, not the extractor. Emergent failure modes go in the open field `modo_falha_relatado` (§5.2), not in the closed catalog.

---

## ADR-029: Cohen's κ per Dimension over a Stratified Double-Coding Sample

- **Context:** §15 requires independent double coding of ≥20% of eligible items and Cohen's κ per dimension (system, evidence level, failure mode, oracle type), with disagreements resolved by a third coder.
- **Decision:** `msrkit coding sample` draws ⌈rate·n⌉ included items per source (at least one), with a recorded seed, into `double_sample.json`; `coding export --double-sample` restricts the sheet to it. `msrkit agreement` computes κ for every coder pair over the items both coded: the screening decision plus the fields listed in `coding.agreement` (or every categorical field with `--all-fields`). A blank `enum`/`bool` is its own category. For `multi` fields, each (item, value) is a binary decision over the values either coder used; κ is pooled over them and also reported per value. Text fields have no κ. Results go to `agreement.json`, with Landis & Koch bands, and `disagreements.csv` lists one row per item and dimension for the third coder.
- **Limit:** κ is undefined (reported as "—") when both coders used a single identical category; the percent agreement is still reported.

---

## ADR-030: PRISMA Flow Derived Only from Run Records

- **Context:** RF8 requires a PRISMA flow. Hand-counted flows drift from the data and cannot be audited.
- **Decision:** `msrkit prisma` derives every number from files the run already produces:
  - identification per source: items plus manifest `discarded`;
  - an estimate of records not retrieved: `estimated_total` minus what was obtained, over queries truncated by a source cap;
  - local-filter removals by reason, from `discarded.jsonl`; the manifest total is authoritative, and any gap is reported as "unspecified";
  - duplicates by pass, from `dedupe_report.json`;
  - screening outcomes, from the final decisions over the screening pool, with exclusion reasons being the E-criteria marked by the excluding coders;
  - included items per source and coded items.

  The output is `data/reports/<run>/prisma.json` and `prisma.md`, which has a Mermaid diagram.
- **Conservative Principle:** a stage without input (dedupe not run) is shown as "n/a", never as zero. Records not retrieved are labelled as an estimate.

---

## ADR-031: Precision on a Stratified Detection Sample

- **Context:** §12.3 requires the extractor's precision to be measured. Detections are dominated by N1 text mentions, so a simple random sample would barely see N2/N3 or the smaller sources.
- **Decision:** `msrkit precision sample` stratifies detections by source × evidence level. It allocates the sample (default 200) proportionally, with at least one per stratum and largest remainders for the rest, and draws it with a recorded seed. The sheet shows each detection's stable key, stratum, evidence and item, plus a `correct` column. `msrkit precision score` reads the judged sheet. It reports precision per stratum and overall, and within each level. Both estimates weight strata by their population size (Σ Wₕ·pₕ), so over-sampled small strata do not bias them. A Wilson 95% interval on the sample is also reported.
- **Limit:** the Wilson interval ignores the stratified design (it is conservative when strata are homogeneous). Judges should record doubtful cases in `note`.

---

## ADR-032: Offline Contract Tests on Recorded Cassettes

- **Context:** §10.2 asks for contract tests that catch API changes. Unit tests use hand-written mocks, which drift from the real APIs silently.
- **Decision:** `tests/test_cassettes.py` replays vcrpy cassettes from `tests/cassettes/` with record mode `none`. Any request not in the cassette fails, so changes in how an adapter calls its API are caught. For each search adapter (GitHub, Stack Exchange, Hacker News, dev.to, Hugging Face), every recorded result must keep the payload keys its normalizer uses, normalize into a valid Item, and yield the same id twice. A separate case covers `msrkit enrich` on this project's own repository. Recording happens only with `MSRKIT_RECORD=1` (mode `once`). Auth headers, key/token parameters and cookies are filtered, and `test_no_secrets_in_cassettes` scans every cassette for tokens. Missing cassettes are skipped with the recording command.
- **Status:** the enrichment cassette is recorded. The search cassettes must be recorded on a machine with open network access, because the development environment only reaches this repository.

---

## ADR-033: CLI Split into a Package by Study Stage

- **Context:** `cli.py` had grown past 2,600 lines with 15 commands and 3 command groups, which made review and navigation slow.
- **Decision:** `msrkit.cli` is a package: `collect`, `evidence`, `review`, `validation`, `corpus` and `menu` hold the commands, and `_common` holds the shared helpers. `app`, `DATA_DIR` and `DEFAULT_PROTOCOL` stay in `msrkit.cli`, and command modules read them at call time (`_cli.DATA_DIR`), so the entry point (`msrkit.cli:app`) and every override (tests monkeypatch `msrkit.cli.DATA_DIR`) keep working. Registration order, and so the `--help` order, is fixed explicitly. Behavior is unchanged: the split was done mechanically from the syntax tree, and the full test suite passes unchanged.

---

## ADR-034: Strict Typing and Formatting Enforced in CI

- **Context:** mypy was configured as strict but never ran in CI. The code had accumulated errors: untyped registries, `str` literals where enums were expected, stale `type: ignore` comments, and a CLI import cycle that hid the type of `app`. Formatting was not checked.
- **Decision:** CI runs `ruff format --check src/ tests/` and `mypy src/` (strict) before the tests. Fixes made:
  - adapter policies use `RedistributionPolicy`;
  - the registry is typed `type[BaseAdapter]`;
  - `BaseAdapter.normalize` declares `terms`;
  - the DuckDB connection is `Any`, since the library is untyped;
  - `app` and `DEFAULT_PROTOCOL` moved to `msrkit.cli._common` to break the import cycle;
  - stale ignores were removed;
  - `types-PyYAML` added to dev dependencies, and `feedparser`/`duckdb` declared as untyped imports.

  Tests are linted and formatted but not type-checked.

---

## ADR-035: Analyses over Units, with an Explicit Basis

- **Context:** A1–A6 (§14) count tools, methods and failure modes. The same repository appears in many items (repo, code hits, issues), and the data comes either from automatic extraction or from manual coding.
- **Decision:** Analyses count *units*: a GitHub repository with all its items merged, or a single item elsewhere. Every analysis takes `--basis detections|coding`, and the basis is part of each output file name. On the coding basis, several coders are combined by consensus. An `enum` value counts only when all coders agree, and a `multi` field keeps the intersection. Disputed values stay out until a third coder resolves them, so the reported numbers are conservative. A1/A2 (`msrkit analyze frequency`) reports units per tool and method. For tools the counts are broken down by best evidence level (disjoint N1/N2/N3); for tools and methods they are also given per system label, where a hybrid unit counts in both, and per source. Co-occurrence within units is reported with Jaccard and lift.

---

## ADR-036: Failure-Mode Coverage Reports Absence

- **Context:** A3 asks which failure modes (FP1–FP13, AF1–AF12) the tools and methods address, for RAG and for agents. §5.2 treats the agent model as a seed, where a mode with no occurrences is a finding.
- **Decision:** `msrkit analyze coverage` runs on the coding basis only, because failure modes are coded manually. It writes:
  - a long table (mode × entry × system, in units);
  - one wide matrix per system (`rag`, `agente`, all), with columns ordered by total;
  - a per-mode summary in catalog order: units, rag/agente units, distinct tools and methods, and the most frequent entries.

  Catalog modes nobody addresses are listed as not covered. Coded modes outside the catalog are appended as "(not in catalog)", so emergent categories stay visible. Units without a system label are kept as "unlabelled".
