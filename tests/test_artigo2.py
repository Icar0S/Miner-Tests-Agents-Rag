"""Article 2 analyses (analysis/artigo2.py) on a synthetic reproducibility package."""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import re
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from msrkit.extract import Detection
from msrkit.models import Engagement, Item, ItemKind, Provenance, TermHit

_PATH = Path(__file__).resolve().parents[1] / "analysis" / "artigo2.py"
_SPEC = importlib.util.spec_from_file_location("artigo2", _PATH)
assert _SPEC and _SPEC.loader
artigo2 = importlib.util.module_from_spec(_SPEC)
sys.modules["artigo2"] = artigo2
_SPEC.loader.exec_module(artigo2)

FETCHED = datetime(2026, 9, 1, tzinfo=UTC)
OLD = datetime(2025, 6, 1, tzinfo=UTC)  # 15 months before collection
RECENT = datetime(2026, 8, 15, tzinfo=UTC)  # below the 3-month minimum age

GAZETTEER = {
    "version": "1",
    "tools": [
        {
            "id": "ragas",
            "name": "Ragas",
            "family": "avaliacao-llm-rag",
            "applies_to": ["rag"],
            "repos": ["explodinggradients/ragas"],
        },
        {
            "id": "deepeval",
            "name": "DeepEval",
            "family": "avaliacao-llm-rag",
            "repos": ["confident-ai/deepeval"],
        },
        {
            "id": "promptfoo",
            "name": "promptfoo",
            "family": "avaliacao-llm-rag",
            "repos": ["promptfoo/promptfoo"],
        },
        {
            "id": "garak",
            "name": "garak",
            "family": "guardrails-seguranca",
            "repos": ["NVIDIA/garak"],
        },
        {
            "id": "webarena",
            "name": "WebArena",
            "family": "agentes",
            "applies_to": ["agente"],
            "repos": ["web-arena-x/webarena"],
        },
        {
            "id": "langchain",
            "name": "LangChain",
            "family": "framework-sut",
            "repos": ["langchain-ai/langchain"],
            "anchor": False,
        },
    ],
    "methods": [
        {"id": "llm-as-judge", "name": "LLM-as-a-judge", "oracle": "pseudo-automatico"},
        {"id": "golden-dataset", "name": "golden dataset", "oracle": "referencia"},
        {"id": "trajectory-evaluation", "name": "trajectory evaluation"},
    ],
}


def _item(
    iid: str,
    source: str,
    kind: ItemKind,
    url: str,
    *,
    created: datetime = OLD,
    concepts: tuple[str, ...] = ("rag",),
    title: str = "Evaluating RAG pipelines",
    query: str = "RAG evaluation",
    pt: bool = False,
    **engagement: int,
) -> Item:
    return Item(
        id=iid,
        source=source,
        kind=kind,
        url=url,
        title=title,
        created_at=created,
        engagement=Engagement(**engagement),
        concepts=["teste", *concepts],
        matched_terms=[TermHit(term="teste de RAG", field="title", context="", lang="pt")]
        if pt
        else [],
        provenance=Provenance(
            run_id="2026-09-01T00-00-00Z-test",
            query_string=query,
            partition="all",
            adapter=source,
            adapter_version="0.1",
            fetched_at=FETCHED,
            response_sha256="0" * 64,
            raw_ref="raw:0",
        ),
    )


def _det(item: Item, tool: str, level: str = "N1", kind: str = "tool") -> Detection:
    signal = {"N1": "text", "N2": "import", "N3": "ci"}[level]
    repo = None
    if item.source == "github":
        parts = str(item.url).split("/")
        repo = f"{parts[3]}/{parts[4]}"
    return Detection(
        item_id=item.id,
        source=item.source,
        repo=repo,
        entry_id=tool,
        entry_type=kind,
        level=level if kind == "tool" else "N1",
        signal=signal if kind == "tool" else "text",
        location="title",
        evidence=tool,
    )


def _corpus() -> tuple[list[Item], list[Detection]]:
    gh = "https://github.com"
    repo = ItemKind.REPO
    items = [
        _item("r-self", "github", repo, f"{gh}/explodinggradients/ragas", stars=9000),
        _item("r-app1", "github", repo, f"{gh}/acme/app1", stars=10),
        _item("r-app2", "github", repo, f"{gh}/acme/app2", stars=5),
        _item("r-app3", "github", repo, f"{gh}/acme/app3", stars=7, concepts=("agente",)),
        _item("r-app4", "github", repo, f"{gh}/acme/app4", stars=3, concepts=("agente",)),
        _item("r-app5", "github", repo, f"{gh}/acme/app5", stars=1),
        _item(
            "i-bot",
            "github",
            ItemKind.ISSUE,
            f"{gh}/acme/app1/issues/1",
            title="Bump ragas from 0.1 to 0.2",
            reactions=0,
        ),
        _item(
            "i-bug",
            "github",
            ItemKind.ISSUE,
            f"{gh}/explodinggradients/ragas/issues/9",
            title="Faithfulness crashes",
            reactions=4,
            comments=6,
            query="kind:issue repo:explodinggradients/ragas is:issue label:bug",
        ),
        _item(
            "i-new",
            "github",
            ItemKind.ISSUE,
            f"{gh}/acme/app2/issues/3",
            created=RECENT,
            reactions=50,
        ),
    ]
    votes = [12, 3, 40, 7, 1, 25, 9, 2]
    for n, v in enumerate(votes):
        concepts = ("agente",) if n % 3 == 0 else ("rag",)
        items.append(
            _item(
                f"hn{n}",
                "hackernews",
                ItemKind.THREAD,
                f"https://news.ycombinator.com/item?id={n}",
                votes=v,
                concepts=concepts,
                pt=n == 0,
            )
        )
    for n, v in enumerate([5, 0, 11, 2, 8, 1]):
        items.append(
            _item(
                f"se{n}",
                "stackexchange",
                ItemKind.THREAD,
                f"https://stackoverflow.com/questions/{n}",
                votes=v,
                views=100,
            )
        )
    for n, r in enumerate([30, 4, 15, 0]):
        items.append(
            _item(
                f"dv{n}",
                "devto",
                ItemKind.ARTICLE,
                f"https://dev.to/a/post-{n}",
                reactions=r,
                concepts=("agente",),
            )
        )
    by_id = {it.id: it for it in items}
    dets = [
        _det(by_id["r-self"], "ragas", "N3"),  # self: set apart
        _det(by_id["r-app1"], "ragas", "N3"),
        _det(by_id["r-app1"], "langchain", "N2"),  # framework: never ranked
        _det(by_id["r-app2"], "deepeval", "N2"),
        _det(by_id["r-app3"], "promptfoo", "N3"),
        _det(by_id["r-app4"], "promptfoo", "N3"),
        _det(by_id["r-app4"], "webarena", "N3"),
        _det(by_id["r-app5"], "deepeval", "N2"),
        _det(by_id["i-bot"], "ragas"),
        _det(by_id["i-bug"], "ragas"),
        _det(by_id["i-new"], "deepeval"),
    ]
    for n in range(8):
        dets.append(_det(by_id[f"hn{n}"], "ragas"))
        if n % 2:
            dets.append(_det(by_id[f"hn{n}"], "deepeval"))
        dets.append(_det(by_id[f"hn{n}"], "llm-as-judge", kind="method"))
    for n in range(6):
        dets.append(_det(by_id[f"se{n}"], "garak" if n < 3 else "deepeval"))
        dets.append(_det(by_id[f"se{n}"], "golden-dataset", kind="method"))
    for n in range(4):
        dets.append(_det(by_id[f"dv{n}"], "promptfoo"))
        dets.append(_det(by_id[f"dv{n}"], "trajectory-evaluation", kind="method"))
    return items, dets


def _write_package(root: Path) -> Path:
    items, dets = _corpus()
    (root / "corpus").mkdir(parents=True)
    (root / "evidence").mkdir()
    (root / "protocol").mkdir()
    (root / "run").mkdir()
    lines = [
        json.dumps({**json.loads(it.model_dump_json()), "redistribution": "metadata_only"})
        for it in items
    ]
    (root / "corpus" / "items.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (root / "evidence" / "detections.jsonl").write_text(
        "\n".join(d.model_dump_json() for d in dets) + "\n", encoding="utf-8"
    )
    repos = [
        {"repo": "acme/app1", "has_ci": True, "workflows": [".github/workflows/ci.yml"]},
        {"repo": "acme/app2", "has_ci": False, "error": "tree HTTP 404"},
    ]
    (root / "evidence" / "github_repos.jsonl").write_text(
        "\n".join(json.dumps(r) for r in repos) + "\n", encoding="utf-8"
    )
    (root / "protocol" / "gazetteer.yaml").write_text(yaml.safe_dump(GAZETTEER), encoding="utf-8")
    manifest = {
        "run_id": "2026-09-01T00-00-00Z-test",
        "protocol_sha256": "ab" * 32,
        "run_options": {"per_query_limit": 10},
        "sources": [
            {
                "name": "hackernews",
                "queries": [{"query_string": "RAG evaluation", "truncated": False}],
            }
        ],
    }
    (root / "run" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "reports").mkdir()
    flow = {
        "identified": {"github": 12, "hackernews": 8},
        "filtered": {"window": 2},
        "duplicates": {"url": 1},
        "screened": 17,
        "included": 0,
        "pending": 17,
        "coded": 0,
    }
    (root / "reports" / "prisma.json").write_text(json.dumps(flow), encoding="utf-8")
    (root / "package_manifest.json").write_text(
        json.dumps({"run_id": manifest["run_id"], "msrkit_version": "0.1.0"}), encoding="utf-8"
    )
    return root


def _rows(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _macros(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    return dict(re.findall(r"\\newcommand\{\\(\w+)\}\{(.*)\}", text))


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    pkg = _write_package(tmp_path / "pkg")
    out = tmp_path / "out"
    code = artigo2.main(
        [str(pkg), "--out", str(out), "--bootstrap", "200", "--min-n", "3", "--min-together", "1"]
    )
    assert code == 0
    return out


def test_pyramid_excludes_frameworks_and_sets_self_apart(run_dir: Path) -> None:
    rows = {r["tool"]: r for r in _rows(run_dir / "qp1_piramide.csv")}
    assert "langchain" not in rows  # framework-sut is context, not a testing tool
    assert rows["ragas"]["self"] == "1"  # explodinggradients/ragas is the tool itself
    assert rows["ragas"]["n3_ext"] == "1"  # only acme/app1 counts as adoption
    assert rows["promptfoo"]["n3_ext"] == "2"
    assert rows["deepeval"]["n2"] == "2"
    # best level per unit: the app1 issue (N1) merges into the app1 repository (N3) and
    # the bug issue merges into the tool's own repository, so N1 holds only the HN items
    assert rows["ragas"]["n1"] == "8"
    assert rows["deepeval"]["n1"] == "7"


def test_macros_define_every_registry_entry_once(run_dir: Path) -> None:
    text = (run_dir / "macros.tex").read_text(encoding="utf-8")
    names = re.findall(r"\\newcommand\{\\(\w+)\}", text)
    assert sorted(names) == sorted(artigo2.MACROS)
    macros = _macros(run_dir / "macros.tex")
    assert macros["ResTopNTres"] == "promptfoo"
    assert macros["ResFwTop"] == "LangChain"
    assert macros["ResItensPt"] == "1"
    assert macros["ResPerQueryLimit"] == "10"
    assert macros["ResReposErro"] == "1"
    assert macros["ResPrismaIdentificados"] == "20"
    assert macros["ResPrismaTriagem"] == "17"
    assert "\\pend" not in text  # a real run never leaves placeholders


def test_kendall_and_summary(run_dir: Path) -> None:
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    tau = summary["kendall"]["UmTres"]
    assert tau["tools"] == 5
    assert -1 <= tau["tau"] <= 1
    assert tau["boot"] > 0 and tau["lo"] <= tau["hi"]
    assert summary["basis"] == "detections"


def test_engagement_filters_and_percentiles(run_dir: Path) -> None:
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    excluded = summary["engagement_excluded"]
    assert excluded["bot"] == 1  # "Bump ragas ..."
    assert excluded["dor"] == 1  # bug issue of the tool's own repository
    assert excluded["idade"] == 1  # issue created two weeks before collection
    tools = {r["entry"]: r for r in _rows(run_dir / "qp3_engajamento_ferramentas.csv")}
    for row in tools.values():
        if row.get("median"):
            assert 0.0 <= float(row["lo"]) <= float(row["median"]) <= float(row["hi"]) <= 1.0
    assert int(tools["ragas"]["n"]) == 8  # HN items only (bot and bug issues excluded)
    pain = _rows(run_dir / "qp3_dor.csv")
    assert pain[0]["tool"] == "ragas" and pain[0]["issues"] == "1"
    assert _macros(run_dir / "macros.tex")["ResItensNaoTruncadas"] == "8"


def test_tables_and_figure_are_latex(run_dir: Path) -> None:
    table = (run_dir / "tab_qp1.tex").read_text(encoding="utf-8")
    assert table.splitlines()[0].startswith("promptfoo & avaliação LLM/RAG & 2 &")
    assert all(line.count("&") == 7 for line in table.splitlines())
    oracles = (run_dir / "tab_qp2_oraculos.tex").read_text(encoding="utf-8")
    assert oracles.count("\\\\") == 5
    figure = (run_dir / "fig_qp1_dispersao.tex").read_text(encoding="utf-8")
    assert "\\begin{axis}" in figure and "coordinates" in figure


def test_zip_package_gives_the_same_result(tmp_path: Path, run_dir: Path) -> None:
    pkg = _write_package(tmp_path / "pkg2")
    archive = tmp_path / "pkg.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in pkg.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(pkg).as_posix())
    out = tmp_path / "out_zip"
    artigo2.main(
        [
            str(archive),
            "--out",
            str(out),
            "--bootstrap",
            "200",
            "--min-n",
            "3",
            "--min-together",
            "1",
        ]
    )
    assert _rows(out / "qp1_piramide.csv") == _rows(run_dir / "qp1_piramide.csv")


def test_pending_writes_placeholders(tmp_path: Path) -> None:
    out = tmp_path / "pending"
    assert artigo2.main(["--pending", "--out", str(out)]) == 0
    macros = _macros(out / "macros.tex")
    assert sorted(macros) == sorted(artigo2.MACROS)
    assert macros["ResItens"] == "\\verificar{11.433}"
    assert macros["ResRho"] == "\\pend{a calcular}"
    for table in artigo2.TABLES:
        assert "\\pend" in (out / f"{table}.tex").read_text(encoding="utf-8")


def test_statistics_helpers() -> None:
    assert artigo2.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert artigo2.cliffs_delta([3, 4, 5], [1, 2, 3]) == pytest.approx(1 - 1 / 9)
    assert artigo2.fmt_int(11433) == "11.433"
    assert artigo2.fmt_float(-0.25) == "\\ensuremath{-}0,25"
    assert artigo2.fmt_float(math.nan) == "n/d"
    assert artigo2.fmt_p(0.0001) == "\\ensuremath{<}\\,0,001"
    kw = artigo2.kruskal_dunn({"a": [0.1, 0.2, 0.3], "b": [0.7, 0.8, 0.9], "c": [0.4, 0.5, 0.6]})
    assert kw["groups"] == 3 and len(kw["pairs"]) == 3
    assert all(p["p_holm"] >= p["p"] for p in kw["pairs"])
    assert artigo2.join_pt(["A", "B", "C"]) == "A, B e C"
