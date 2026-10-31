"""Open discovery pass (Protocol E2 v2, §7.6; analysis A5): LDA topics and clusters.

The gazetteer anchors collection and extraction, which biases the catalog
toward what was already known. This pass looks at the text of the included
items without the gazetteer: LDA topics over term counts and k-means clusters
over TF-IDF. Top terms that match no gazetteer name and no protocol lexicon
term are flagged as *candidates* for manual inspection; confirmed ones enter
the gazetteer with `origin: discovery`, so the report can state which fraction
of the catalog came from this pass.

Requires the optional extra: `pip install -e ".[analysis]"` (scikit-learn).
Both models use a fixed seed, so results are reproducible for a given corpus.
"""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from msrkit.gazetteer import Gazetteer, MethodEntry, ToolEntry
    from msrkit.models import Item

DEFAULT_SEED = 20260928
MAX_CHARS = 5000
# Portuguese function words (scikit-learn ships only an English list).
PT_STOPWORDS = """
a ao aos as até com como da das de dela dele do dos e é ela ele em entre era essa esse
esta este eu foi for há isso isto já lhe mais mas me mesmo meu minha muito na nas nem no
nos o os ou para pela pelas pelo pelos por qual quando que se sem ser seu sua são também
te tem ter um uma umas uns você vocês
""".split()  # noqa: SIM905
# Markup and platform noise that survives in bodies, and contractions whose
# apostrophe the tokenizer drops ("won't" -> "wont").
NOISE = """
http https www com github img src href png jpg nbsp amp quot rel nofollow
dont wont cant isnt doesnt didnt im ive youre thats theres
""".split()  # noqa: SIM905
# Documents shorter than this (after cleaning) are left out: a repository with
# only a name gives the models nothing to work with and distorts k-means.
MIN_WORDS = 15


def require_sklearn() -> None:
    try:
        import sklearn  # noqa: F401
    except ImportError as e:  # pragma: no cover - depends on the environment
        raise RuntimeError(
            'The discovery pass needs scikit-learn: pip install -e ".[analysis]"'
        ) from e


def document(item: Item) -> str:
    """Title and body as plain prose: no code, markup, entities or URLs."""
    text = f"{item.title or ''}\n{item.body or ''}"
    text = re.sub(r"`{3}.*?`{3}", " ", text, flags=re.S)  # fenced code (Markdown)
    text = re.sub(r"<(pre|code)\b.*?</\1>", " ", text, flags=re.S | re.I)  # code (HTML)
    text = re.sub(r"<[^>]+>", " ", text)  # remaining tags
    text = html.unescape(text)  # &quot; &#x2F; ...
    text = re.sub(r"https?://\S+", " ", text)
    return " ".join(text.split())[:MAX_CHARS]


def known_vocabulary(gazetteer: Gazetteer | None, lexicon: list[str]) -> set[str]:
    """Lowercased tokens of every gazetteer name/alias/id and lexicon term."""
    names: list[str] = list(lexicon)
    if gazetteer is not None:
        for tool in gazetteer.tools:
            names += [tool.id, *tool.names(), *tool.packages]
        for method in gazetteer.methods:
            names += [method.id, *method.names()]
    tokens: set[str] = set()
    for name in names:
        tokens |= set(re.findall(r"[a-z0-9]+", name.lower()))
    return tokens


def is_candidate(term: str, known: set[str]) -> bool:
    return not set(term.split()) & known


class Topic(BaseModel):
    topic: int
    documents: int  # items whose dominant topic this is
    top_terms: list[str]
    candidates: list[str]  # top terms outside the gazetteer and the lexicon


class Cluster(BaseModel):
    cluster: int
    size: int
    top_terms: list[str]
    candidates: list[str]
    examples: list[str]  # titles of the items closest to the centroid


class Assignment(BaseModel):
    item_id: str
    topic: int
    topic_weight: float
    cluster: int


class DiscoveryResult(BaseModel):
    documents: int
    skipped_short: int = 0  # documents under MIN_WORDS after cleaning
    vocabulary: int
    topics: list[Topic]
    clusters: list[Cluster]
    assignments: list[Assignment]
    candidate_terms: list[str]  # union, ordered by first appearance


def discover(
    items: list[Item],
    known: set[str],
    n_topics: int = 10,
    n_clusters: int = 10,
    top_n: int = 12,
    min_df: int = 2,
    seed: int = DEFAULT_SEED,
) -> DiscoveryResult:
    require_sklearn()
    from sklearn.cluster import KMeans
    from sklearn.decomposition import LatentDirichletAllocation
    from sklearn.feature_extraction.text import (
        ENGLISH_STOP_WORDS,
        CountVectorizer,
        TfidfVectorizer,
    )

    pairs = [(it, document(it)) for it in items]
    kept = [(it, d) for it, d in pairs if len(d.split()) >= MIN_WORDS]
    skipped_short = len(pairs) - len(kept)
    items = [it for it, _ in kept]
    docs = [d for _, d in kept]
    if len(docs) < max(n_topics, n_clusters, 2):
        raise ValueError(f"{len(docs)} documents: need at least as many as topics and clusters")
    stop = sorted(set(ENGLISH_STOP_WORDS) | set(PT_STOPWORDS) | set(NOISE))
    common: dict[str, Any] = {
        "stop_words": stop,
        "ngram_range": (1, 2),
        "min_df": min_df,
        "max_df": 0.9,
        "token_pattern": r"(?u)\b[a-zA-Z][a-zA-Z0-9_-]+\b",
        "lowercase": True,
    }
    counts_vec = CountVectorizer(**common)
    counts = counts_vec.fit_transform(docs)
    vocab = counts_vec.get_feature_names_out()
    if len(vocab) == 0:
        raise ValueError("empty vocabulary after stop words and min_df")

    lda = LatentDirichletAllocation(
        n_components=n_topics, random_state=seed, learning_method="batch"
    )
    doc_topics = lda.fit_transform(counts)
    dominant = doc_topics.argmax(axis=1)

    tfidf_vec = TfidfVectorizer(**common)
    tfidf = tfidf_vec.fit_transform(docs)
    tfidf_vocab = tfidf_vec.get_feature_names_out()
    km = KMeans(n_clusters=n_clusters, random_state=seed, n_init=10)
    labels = km.fit_predict(tfidf)
    distances = km.transform(tfidf)

    candidates: dict[str, None] = {}
    topics = []
    for k, weights in enumerate(lda.components_):
        terms = [str(vocab[i]) for i in weights.argsort()[::-1][:top_n]]
        cands = [t for t in terms if is_candidate(t, known)]
        candidates.update(dict.fromkeys(cands))
        topics.append(
            Topic(topic=k, documents=int((dominant == k).sum()), top_terms=terms, candidates=cands)
        )
    clusters = []
    for k, centre in enumerate(km.cluster_centers_):
        terms = [str(tfidf_vocab[i]) for i in centre.argsort()[::-1][:top_n]]
        cands = [t for t in terms if is_candidate(t, known)]
        candidates.update(dict.fromkeys(cands))
        members = [i for i, lab in enumerate(labels) if lab == k]
        closest = sorted(members, key=lambda i: distances[i, k])[:3]
        clusters.append(
            Cluster(
                cluster=k,
                size=len(members),
                top_terms=terms,
                candidates=cands,
                examples=[items[i].title or items[i].id for i in closest],
            )
        )
    assignments = [
        Assignment(
            item_id=it.id,
            topic=int(dominant[i]),
            topic_weight=round(float(doc_topics[i, dominant[i]]), 4),
            cluster=int(labels[i]),
        )
        for i, it in enumerate(items)
    ]
    return DiscoveryResult(
        documents=len(docs),
        skipped_short=skipped_short,
        vocabulary=len(vocab),
        topics=topics,
        clusters=clusters,
        assignments=assignments,
        candidate_terms=list(candidates),
    )


def catalog_origin(gazetteer: Gazetteer) -> dict[str, int]:
    """Entries of the catalog by origin (seed vs. discovery pass)."""
    out = {"seed": 0, "discovery": 0}
    entries: list[ToolEntry | MethodEntry] = [*gazetteer.tools, *gazetteer.methods]
    for entry in entries:
        out[entry.origin] += 1
    return out
