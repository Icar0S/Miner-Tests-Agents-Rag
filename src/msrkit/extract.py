"""Tool and method extraction with evidence levels (Protocol E2 v2, §3.4 and §12.1).

Signals and the level each one supports:

| Signal       | Where                                                    | Level |
|--------------|----------------------------------------------------------|-------|
| `text`       | tool/method name in an item's title, body or tags        | N1    |
| `import`     | code search hit whose literal query is an import of it   | N2    |
| `config`     | tool config file in the repository tree or as a hit      | N2    |
| `dependency` | tool package in a root dependency manifest               | N2    |
| `ci`         | tool invoked in a CI workflow file                       | N3*   |

*A CI invocation is N3 only when the repository also has commits in ≥2
distinct months of the window and ≥2 contributors; otherwise it counts as N2.

Methods are detected by name in text only (N1). Repository-level signals come
from `msrkit enrich` and are attached to the repository item (or, if the run has
none, to the first item of that repository).
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from msrkit.enrich import repo_of
from msrkit.keywords import match_terms
from msrkit.models import ItemKind

if TYPE_CHECKING:
    from msrkit.enrich import RepoSignals
    from msrkit.gazetteer import Gazetteer, ToolEntry
    from msrkit.models import Item

Level = Literal["N1", "N2", "N3"]
Signal = Literal["text", "import", "config", "dependency", "ci"]
LEVEL_RANK = {"N1": 1, "N2": 2, "N3": 3}
N3_MIN_MONTHS = 2
N3_MIN_CONTRIBUTORS = 2
EVIDENCE_MAX = 300


class Detection(BaseModel):
    """One tool or method found in one item, with the evidence that supports it."""

    item_id: str
    source: str
    repo: str | None = None
    entry_id: str
    entry_type: Literal["tool", "method"]
    level: Level
    signal: Signal
    location: str  # item field, file path or query
    evidence: str  # at most EVIDENCE_MAX characters
    # For ambiguous tool names (§12.2): what confirmed the mention.
    confirmed_by: Literal["", "context", "structural"] = ""


def _norm_pkg(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= EVIDENCE_MAX else text[: EVIDENCE_MAX - 1] + "…"


def dependency_names(path: str, content: str) -> dict[str, str]:
    """Normalized dependency name -> the line that declares it."""
    names: dict[str, str] = {}
    base = PurePosixPath(path).name
    if base == "package.json":
        try:
            data = json.loads(content)
        except ValueError:
            return names
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            for dep, version in (data.get(key) or {}).items():
                names.setdefault(_norm_pkg(dep), f'"{dep}": "{version}"')
        return names
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if base.endswith(".txt"):
            match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)", stripped)
            candidates = [match.group(1)] if match else []
        else:  # pyproject.toml / setup.cfg / setup.py: quoted specs and `name = ` keys
            candidates = re.findall(
                r'["\']([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*[<>=!~;"\']', stripped
            )
            key = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)\s*=", stripped)
            if key:
                candidates.append(key.group(1))
        for cand in candidates:
            names.setdefault(_norm_pkg(cand), stripped)
    return names


def _import_patterns(tool: ToolEntry) -> list[re.Pattern[str]]:
    return [
        re.compile(
            rf"(?:^|\W)(?:from\s+{re.escape(mod)}(?:\.\w+)*\s+import|import\s+{re.escape(mod)}\b)"
        )
        for mod in tool.python_imports
    ]


def _ci_patterns(tool: ToolEntry) -> list[re.Pattern[str]]:
    commands = [*tool.cli, *(f"python -m {m}" for m in tool.python_imports)]
    return [re.compile(rf"(?<![\w-]){re.escape(c)}(?![\w-])", re.IGNORECASE) for c in commands]


class Extractor:
    """Applies the gazetteer to items and repository signals."""

    def __init__(self, gazetteer: Gazetteer, context_terms: list[str] | None = None) -> None:
        self.gazetteer = gazetteer
        # Lexicon that confirms an ambiguous name when found in its context window.
        self.context_terms = context_terms or []
        self.dropped_ambiguous = 0  # ambiguous mentions without confirmation, set by run()
        self._pending: list[Detection] = []

    # -- item-level ----------------------------------------------------------

    def from_item(self, item: Item) -> list[Detection]:
        repo = repo_of(item)
        out: list[Detection] = []
        text_fields = {
            "title": item.title,
            "body": item.body,
            "tags": " ".join(item.tech.tags) or None,
        }

        for tool in self.gazetteer.tools:
            hits = match_terms(
                tool.names(),
                title=text_fields["title"],
                body=text_fields["body"],
                tags=item.tech.tags or None,
                mode="exact",
            )
            if not hits:
                continue
            if not tool.ambiguous:
                out.append(
                    self._det(
                        item, repo, tool.id, "tool", "N1", "text", hits[0].field, hits[0].context
                    )
                )
                continue
            confirmed = next(
                (
                    h
                    for h in hits
                    if self.context_terms and match_terms(self.context_terms, body=h.context)
                ),
                None,
            )
            det = self._det(
                item,
                repo,
                tool.id,
                "tool",
                "N1",
                "text",
                (confirmed or hits[0]).field,
                (confirmed or hits[0]).context,
            )
            if confirmed is not None:
                out.append(det.model_copy(update={"confirmed_by": "context"}))
            else:
                self._pending.append(det)  # kept only with a structural signal (see run)
        for method in self.gazetteer.methods:
            hits = match_terms(
                method.names(), title=item.title, body=item.body, tags=item.tech.tags or None
            )
            if hits:
                out.append(
                    self._det(
                        item,
                        repo,
                        method.id,
                        "method",
                        "N1",
                        "text",
                        hits[0].field,
                        hits[0].context,
                    )
                )

        if item.kind == ItemKind.CODE:
            out.extend(self._from_code_hit(item, repo))
        return out

    def _from_code_hit(self, item: Item, repo: str | None) -> list[Detection]:
        """A code search hit contains the literal query string (GitHub guarantees the match)."""
        query = item.provenance.query_string
        path = item.tech.path or item.title or ""
        name = PurePosixPath(path).name.lower()
        out: list[Detection] = []
        for tool in self.gazetteer.tools:
            if any(p.search(query) for p in _import_patterns(tool)):
                out.append(self._det(item, repo, tool.id, "tool", "N2", "import", path, query))
            if name in {c.lower() for c in tool.config_files}:
                out.append(self._det(item, repo, tool.id, "tool", "N2", "config", path, name))
        return out

    # -- repository-level ----------------------------------------------------

    def from_repo(self, sig: RepoSignals, item: Item) -> list[Detection]:
        out: list[Detection] = []
        if sig.error:
            return out
        sustained = (
            len(sig.commit_months) >= N3_MIN_MONTHS
            and (sig.contributors or 0) >= N3_MIN_CONTRIBUTORS
        )
        deps: dict[str, tuple[str, str]] = {}
        for path, content in sig.manifests.items():
            for dep, line in dependency_names(path, content).items():
                deps.setdefault(dep, (path, line))
        config_names = {PurePosixPath(p).name.lower(): p for p in sig.config_paths}

        for tool in self.gazetteer.tools:
            for pkg in tool.packages:
                if _norm_pkg(pkg) in deps:
                    path, line = deps[_norm_pkg(pkg)]
                    out.append(
                        self._det(item, sig.repo, tool.id, "tool", "N2", "dependency", path, line)
                    )
                    break
            for cfg in tool.config_files:
                if cfg.lower() in config_names:
                    path = config_names[cfg.lower()]
                    out.append(
                        self._det(item, sig.repo, tool.id, "tool", "N2", "config", path, path)
                    )
                    break
            patterns = _ci_patterns(tool)
            for wf_path, content in sig.workflows.items():
                line = next(
                    (ln for ln in content.splitlines() if any(p.search(ln) for p in patterns)), None
                )
                if line is not None:
                    level: Level = "N3" if sustained else "N2"
                    out.append(
                        self._det(item, sig.repo, tool.id, "tool", level, "ci", wf_path, line)
                    )
                    break
        return out

    # -- run-level -----------------------------------------------------------

    def run(self, items: list[Item], signals: dict[str, RepoSignals]) -> list[Detection]:
        detections: list[Detection] = []
        self._pending = []
        anchor_item: dict[str, Item] = {}
        for item in items:
            detections.extend(self.from_item(item))
            repo = repo_of(item)
            if repo:
                current = anchor_item.get(repo)
                if current is None or (
                    current.kind != ItemKind.REPO and item.kind == ItemKind.REPO
                ):
                    anchor_item[repo] = item
        for repo, sig in signals.items():
            if repo in anchor_item:
                detections.extend(self.from_repo(sig, anchor_item[repo]))

        structural = {(d.entry_id, d.repo) for d in detections if d.signal != "text" and d.repo}
        self.dropped_ambiguous = 0
        for det in self._pending:
            if det.repo and (det.entry_id, det.repo) in structural:
                detections.append(det.model_copy(update={"confirmed_by": "structural"}))
            else:
                self.dropped_ambiguous += 1
        self._pending = []
        return detections

    @staticmethod
    def _det(
        item: Item,
        repo: str | None,
        entry_id: str,
        entry_type: Literal["tool", "method"],
        level: Level,
        signal: Signal,
        location: str | None,
        evidence: str,
    ) -> Detection:
        return Detection(
            item_id=item.id,
            source=item.source,
            repo=repo,
            entry_id=entry_id,
            entry_type=entry_type,
            level=level,
            signal=signal,
            location=location or "",
            evidence=_clip(evidence),
        )


def summarize(detections: list[Detection]) -> dict[str, dict[str, int]]:
    """Per entry: number of distinct units (repository, or item outside GitHub) at each level.

    Each unit counts once per entry, at the highest level reached — so N1, N2 and N3
    are disjoint counts, as §3.4 requires for reporting.
    """
    best: dict[tuple[str, str], str] = {}
    for d in detections:
        key = (d.entry_id, d.repo or f"item:{d.item_id}")
        if LEVEL_RANK[d.level] > LEVEL_RANK.get(best.get(key, ""), 0):
            best[key] = d.level
    table: dict[str, dict[str, int]] = {}
    for (entry, _unit), level in best.items():
        row = table.setdefault(entry, {"N1": 0, "N2": 0, "N3": 0})
        row[level] += 1
    return table
