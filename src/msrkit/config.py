"""Configuration loader: YAML protocol file → Pydantic models.

Loads, validates, and provides typed access to the research protocol
defined in YAML files.
"""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, PrivateAttr, field_validator, model_validator

from msrkit.gazetteer import Gazetteer, load_gazetteer
from msrkit.models import Query

# ---------------------------------------------------------------------------
# Protocol config models
# ---------------------------------------------------------------------------


class WindowConfig(BaseModel):
    """Temporal window for the collection."""

    since: str  # YYYY-MM-DD
    until: str  # YYYY-MM-DD

    @field_validator("since", "until")
    @classmethod
    def _validate_date_format(cls, v: str) -> str:
        """Ensure date strings are in YYYY-MM-DD format."""
        from datetime import date as date_type

        try:
            date_type.fromisoformat(v)
        except ValueError as e:
            raise ValueError(f"Invalid date format '{v}', expected YYYY-MM-DD") from e
        return v


class RawQueryConfig(BaseModel):
    """A literal query in the source's own search syntax (Protocol E2 v2, §7.3)."""

    q: str
    kind: str | None = None  # e.g. repo | code | issue for GitHub
    label: str = ""
    evidence: Literal["N1", "N2", "N3"] | None = None  # evidence level the query targets
    # Families of gazetteer anchors that `{anchor}` / `{repo}` expand over (None: all).
    anchor_families: list[str] | None = None


class SourceConfig(BaseModel):
    """Configuration for a single source in the protocol."""

    enabled: bool = False
    kinds: list[str] = []
    extra: dict[str, Any] = {}
    # Literal per-source queries, run in addition to (or instead of) the lexicon.
    queries: list[RawQueryConfig] = []
    use_terms: bool = True  # False: run only `queries`


class LimitsConfig(BaseModel):
    """Global limits for the collection run."""

    max_items_per_source: int = 5000
    max_requests_per_source: int = 2000
    stop_on_quota_exhausted: bool = True


class ProtocolConfig(BaseModel):
    """Full research protocol configuration."""

    version: int
    name: str
    description: str
    window: WindowConfig
    terms: list[str]  # base lexicon, in English
    # Additional lexicons per language, e.g. {"pt": ["teste de RAG", ...]}.
    terms_by_language: dict[str, list[str]] = {}
    languages: list[str] = ["en"]
    sources: dict[str, SourceConfig]
    limits: LimitsConfig = LimitsConfig()
    # Concept groups with their own lexicons, e.g. {"rag": [...], "agente": [...],
    # "teste": [...]}; items are labeled with every concept whose lexicon matches.
    concepts: dict[str, list[str]] = {}
    # Optional query terms built as the product of concept lexicons, e.g.
    # [["rag", "teste"]] adds "<rag term> <teste term>" for every pair.
    concept_queries: list[list[str]] = []
    # Path to the gazetteer YAML, relative to the protocol file (§7.2).
    gazetteer: str | None = None

    _gazetteer: Gazetteer | None = PrivateAttr(default=None)

    @property
    def gazetteer_data(self) -> Gazetteer | None:
        """The loaded gazetteer, if the protocol names one."""
        return self._gazetteer

    @model_validator(mode="after")
    def _check_term_languages(self) -> ProtocolConfig:
        """Every extra lexicon must belong to a language declared in `languages`."""
        undeclared = sorted(set(self.terms_by_language) - set(self.languages))
        if undeclared:
            raise ValueError(
                f"terms_by_language has languages not listed in `languages`: {undeclared}"
            )
        return self

    @model_validator(mode="after")
    def _check_concept_queries(self) -> ProtocolConfig:
        for combo in self.concept_queries:
            unknown = sorted(set(combo) - set(self.concepts))
            if unknown:
                raise ValueError(f"concept_queries uses undefined concepts: {unknown}")
        return self

    def all_terms(self) -> list[str]:
        """Base terms, language lexicons and concept combinations, deduplicated, in order."""
        seen: dict[str, None] = dict.fromkeys(self.terms)
        for lexicon in self.terms_by_language.values():
            seen.update(dict.fromkeys(lexicon))
        for combo in self.concept_queries:
            for parts in itertools.product(*(self.concepts[c] for c in combo)):
                seen[" ".join(parts)] = None
        return list(seen)

    def term_languages(self) -> dict[str, str]:
        """Map each term to the language of the lexicon it came from."""
        mapping = dict.fromkeys(self.terms, "en")
        for lang, lexicon in self.terms_by_language.items():
            for term in lexicon:
                mapping.setdefault(term, lang)
        return mapping

    def _expand_template(self, raw_q: RawQueryConfig) -> list[tuple[str, str]]:
        """Expand `{anchor}` / `{repo}` over gazetteer anchors; plain queries pass through."""
        if "{anchor}" not in raw_q.q and "{repo}" not in raw_q.q:
            return [(raw_q.q, raw_q.label)]
        if self._gazetteer is None:
            raise ValueError(f"query '{raw_q.q}' uses a template but no gazetteer is loaded")
        out: list[tuple[str, str]] = []
        for tool in self._gazetteer.anchors(raw_q.anchor_families):
            repos = tool.repos if "{repo}" in raw_q.q else [""]
            for repo in repos:
                text = raw_q.q.replace("{anchor}", tool.token()).replace("{repo}", repo)
                out.append((text, f"{raw_q.label}:{tool.id}" if raw_q.label else tool.id))
        return out

    def enabled_sources(self) -> list[str]:
        """Return names of all enabled sources."""
        return [name for name, cfg in self.sources.items() if cfg.enabled]

    def build_queries(self, source_name: str) -> list[Query]:
        """Build Query objects for a given source from protocol config."""
        from datetime import date

        src_cfg = self.sources.get(source_name)
        if src_cfg is None or not src_cfg.enabled:
            return []

        since = date.fromisoformat(self.window.since)
        until = date.fromisoformat(self.window.until)

        queries: list[Query] = []
        # Se a fonte tem kinds configurados, cria uma query por kind
        kinds = src_cfg.kinds if src_cfg.kinds else [None]

        if src_cfg.use_terms:
            for kind in kinds:
                queries.append(
                    Query(
                        source=source_name,
                        terms=self.all_terms(),
                        kind=kind,
                        since=since,
                        until=until,
                        extra=src_cfg.extra,
                        limit=self.limits.max_items_per_source,
                    )
                )

        for raw_q in src_cfg.queries:
            for text, label in self._expand_template(raw_q):
                queries.append(
                    Query(
                        source=source_name,
                        terms=[text],
                        kind=raw_q.kind,
                        since=since,
                        until=until,
                        extra=src_cfg.extra,
                        limit=self.limits.max_items_per_source,
                        raw=True,
                        label=label,
                    )
                )

        return queries


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_protocol(path: str | Path) -> ProtocolConfig:
    """Load and validate a protocol YAML file.

    Args:
        path: Path to the YAML protocol file.

    Returns:
        Validated ProtocolConfig instance.

    Raises:
        FileNotFoundError: If the protocol file does not exist.
        ValueError: If the YAML content is invalid.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Protocol file not found: {p}")

    raw = p.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)

    if not isinstance(data, dict):
        raise ValueError(f"Protocol file must contain a YAML mapping, got {type(data).__name__}")

    config = ProtocolConfig.model_validate(data)
    if config.gazetteer:
        config._gazetteer = load_gazetteer(p.parent / config.gazetteer)
    return config


def protocol_sha256(path: str | Path) -> str:
    """Compute SHA-256 hash of a protocol file for provenance."""
    content = Path(path).read_bytes()
    return hashlib.sha256(content).hexdigest()
