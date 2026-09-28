"""Reproducibility package for Zenodo (Phase 2, V4)."""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from msrkit.cli import app
from msrkit.models import Item, ItemKind, Provenance, TermHit
from msrkit.package import build_package, pseudonym, redact_item
from msrkit.storage import ItemStorage

if TYPE_CHECKING:
    from pathlib import Path

PROTO = "protocols/v0_rag_agents_testing.yaml"


def _item(iid: str, source: str) -> Item:
    return Item(
        id=iid,
        source=source,
        kind=ItemKind.THREAD,
        url=f"https://x.example/{iid}",  # type: ignore[arg-type]
        title="How to test RAG",
        body="full text of the post",
        author_handle="alice",
        matched_terms=[TermHit(term="rag", field="body", context="test RAG pipelines")],
        provenance=Provenance(
            run_id="r",
            query_string="q",
            partition="p",
            adapter="a",
            adapter_version="1",
            fetched_at=datetime.now(UTC),
            response_sha256="",
            raw_ref="",
        ),  # fmt: skip
    )


def _zip(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


class TestRedaction:
    def test_metadata_only_drops_text_and_keeps_hash(self) -> None:
        item = _item("a", "hackernews")
        data = redact_item(item, "metadata_only", keep_authors=False, salt="s")
        assert data["body"] is None and data["body_hash"] == item.body_hash
        assert data["matched_terms"][0]["context"] == ""
        assert data["author_handle"] == pseudonym("alice", "s") != "alice"
        assert data["redistribution"] == "metadata_only"

    def test_full_text_with_attribution_keeps_text(self) -> None:
        data = redact_item(_item("b", "stackexchange"), "full_text_with_attribution", True, "s")
        assert data["body"] == "full text of the post"
        assert data["author_handle"] == "alice"


class TestBuild:
    def test_contents_checksums_and_repo_contents_stripped(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        (data / "runs" / "r1").mkdir(parents=True)
        (data / "runs" / "r1" / "manifest.json").write_text("{}")
        enrich = data / "enrich" / "r1"
        enrich.mkdir(parents=True)
        (enrich / "github_repos.jsonl").write_text(
            json.dumps(
                {
                    "repo": "o/r",
                    "workflows": {"ci.yml": "SECRET CODE"},
                    "manifests": {"pyproject.toml": "x"},
                }
            )
            + "\n"  # fmt: skip
        )
        (data / "reports" / "r1").mkdir(parents=True)
        (data / "reports" / "r1" / "prisma.md").write_text("# flow")
        proto = tmp_path / "p.yaml"
        proto.write_text("name: p")
        gaz = tmp_path / "gaz.yaml"
        gaz.write_text("version: '1'")

        out, contents = build_package(
            data, "r1", tmp_path / "pkg.zip",
            [_item("a", "hackernews"), _item("b", "stackexchange")],
            {"stackexchange": "full_text_with_attribution"},
            proto, [gaz], salt="s", creators=["Doe, Jane"],
        )  # fmt: skip
        files = _zip(out)
        assert {"protocol/p.yaml", "protocol/gaz.yaml", "run/manifest.json",
                "corpus/items.jsonl", "reports/prisma.md", "README.md", ".zenodo.json",
                "package_manifest.json", "SHA256SUMS"} <= set(files)  # fmt: skip
        assert "run/discarded.jsonl" not in files  # absent inputs are skipped
        assert b"SECRET CODE" not in files["evidence/github_repos.jsonl"]
        assert json.loads(files["evidence/github_repos.jsonl"])["workflows"] == ["ci.yml"]
        items = [json.loads(ln) for ln in files["corpus/items.jsonl"].decode().splitlines()]
        assert [it["body"] for it in items] == [None, "full text of the post"]
        for line in files["SHA256SUMS"].decode().splitlines():
            digest, name = line.split("  ")
            assert hashlib.sha256(files[name]).hexdigest() == digest
        zen = json.loads(files[".zenodo.json"])
        assert zen["creators"] == [{"name": "Doe, Jane"}]
        assert contents["protocol"] == 2

    def test_zip_entries_have_fixed_timestamps(self, tmp_path: Path) -> None:
        data = tmp_path / "data"
        out, _ = build_package(data, "r", tmp_path / "a.zip", [], {}, None, salt="s")
        with zipfile.ZipFile(out) as zf:
            assert {i.date_time for i in zf.infolist()} == {(2026, 1, 1, 0, 0, 0)}


class TestPackageCommand:
    def test_package(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)
        ItemStorage(tmp_path).save_items([_item("a", "stackexchange")], "run-z")
        r = CliRunner().invoke(app, ["package", "--run", "run-z", "-p", PROTO])
        assert r.exit_code == 0, r.stdout
        files = _zip(tmp_path / "packages" / "msrkit_run-z.zip")
        assert "protocol/v0_rag_agents_testing.yaml" in files
        assert "protocol/gazetteer.yaml" in files
        item = json.loads(files["corpus/items.jsonl"])
        assert item["redistribution"] == "full_text_with_attribution"
        assert item["author_handle"].startswith("anon-")
