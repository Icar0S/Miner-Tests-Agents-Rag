"""Tests for the Textual TUI screening interface."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from msrkit.models import Item, ItemKind, Provenance, TermHit
from msrkit.screening import ScreeningCriteria
from msrkit.tui import ScreeningApp

if TYPE_CHECKING:
    from pathlib import Path


def _make_item(idx: int = 1) -> Item:
    return Item(
        id=f"item_{idx}",
        source="github",
        kind=ItemKind.REPO,
        url="https://github.com/example/rag-test",
        title=f"RAG Testing Framework {idx}",
        body="Evaluating RAG agents with golden dataset and test oracles.",
        matched_terms=[
            TermHit(term="RAG testing", field="title", context="RAG testing"),
            TermHit(term="golden dataset", field="body", context="golden dataset"),
        ],
        provenance=Provenance(
            run_id="test-run",
            query_string="q",
            partition="p",
            adapter="github",
            adapter_version="0.1.0",
            fetched_at=datetime.now(UTC),
            response_sha256="abc",
            raw_ref="ref",
        ),
    )


def test_tui_app_mount_and_include(tmp_path: Path) -> None:
    import asyncio

    async def _run() -> None:
        item1 = _make_item(1)
        item2 = _make_item(2)
        criteria = ScreeningCriteria(
            inclusion={"I1": "Trata de teste de RAG"},
            exclusion={"E1": "LLM4Testing"},
            batch_size=10,
        )
        app = ScreeningApp(
            items=[item1, item2],
            criteria=criteria,
            data_dir=tmp_path,
            run_id="test-run",
            coder="joao",
        )

        async with app.run_test() as pilot:
            assert app.is_running
            assert app.current_idx == 0

            # Pressiona 'i' para incluir o primeiro item
            await pilot.press("i")
            assert len(app.recorded_decisions) == 1
            assert app.recorded_decisions[0].decision == "include"
            assert app.recorded_decisions[0].item_id == "item_1"
            assert app.current_idx == 1

            # Pressiona 'u' para marcar dúvida no segundo item
            await pilot.press("u")
            assert len(app.recorded_decisions) == 2
            assert app.recorded_decisions[1].decision == "uncertain"
            assert app.recorded_decisions[1].item_id == "item_2"

            # Pressiona 'q' para salvar e sair
            await pilot.press("q")

        # Verifica se as decisões foram persistidas em disco
        from msrkit.screening import load_decisions

        decisions = load_decisions(tmp_path, "test-run")
        assert ("item_1", "joao") in decisions
        assert ("item_2", "joao") in decisions
        assert decisions[("item_1", "joao")].decision == "include"
        assert decisions[("item_2", "joao")].decision == "uncertain"

    asyncio.run(_run())
