"""Tests for the Textual TUI screening interface and Annex A Coding TUI."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from msrkit.models import Item, ItemKind, Provenance, TermHit
from msrkit.screening import ScreeningCriteria
from msrkit.tui import CodingApp, ScreeningApp

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _make_item(idx: int = 1) -> Item:
    item = Item(
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
    item.engagement.stars = 120
    item.engagement.forks = 15
    item.tech.has_ci = True
    item.tech.has_tests = True
    item.tech.contributors = 8
    item.tech.tags = ["rag", "agents", "testing"]
    return item


def test_tui_app_mount_and_include(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)

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

            # Verifica exibição de sinais técnicos no Label #item-tech
            tech_lbl = app.query_one("#item-tech")
            assert "120 estrelas" in str(tech_lbl.render())
            assert "CI/CD: [✓ Sim]" in str(tech_lbl.render())
            assert "Testes: [✓ Sim]" in str(tech_lbl.render())

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

        # Verifica compilação automática do fluxo PRISMA 2020
        assert (tmp_path / "reports" / "test-run" / "prisma.tikz").exists()
        assert (tmp_path / "reports" / "test-run" / "prisma.md").exists()

    asyncio.run(_run())


def test_coding_app_mount_and_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)

    async def _run() -> None:
        item1 = _make_item(1)
        sug_map = {
            "item_1": {
                "auto_sistema": "rag",
                "auto_nivel": "N2",
                "auto_ferramentas": "ragas, deepeval",
            }
        }
        app = CodingApp(
            items=[item1],
            data_dir=tmp_path,
            run_id="test-run",
            coder="joao",
            suggestions_map=sug_map,
        )

        async with app.run_test() as pilot:
            assert app.is_running
            assert app.current_idx == 0

            # Verifica campos preenchidos e sinais
            tech_lbl = app.query_one("#coding-tech")
            assert "120 estrelas" in str(tech_lbl.render())

            sug_lbl = app.query_one("#coding-suggestions")
            assert "ragas" in str(sug_lbl.render())

            # Pressiona 'g' para gravar a codificação
            await pilot.press("g")
            assert len(app.recorded_codings) == 1
            assert app.recorded_codings[0].item_id == "item_1"
            assert app.recorded_codings[0].values["sistema"] == ["rag"]

            # Pressiona 'q' para sair e salvar
            await pilot.press("q")

        from msrkit.coding import load_codings

        codings = load_codings(tmp_path, "test-run")
        assert ("item_1", "joao") in codings
        assert codings[("item_1", "joao")].values["sistema"] == ["rag"]

        # PRISMA compilado automaticamente com itens codificados
        assert (tmp_path / "reports" / "test-run" / "prisma.tikz").exists()

    asyncio.run(_run())


def test_coding_tui_cli_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from msrkit.cli import app
    from msrkit.screening import ScreeningDecision, append_decisions
    from msrkit.storage import ItemStorage

    monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)

    item1 = _make_item(1)
    ItemStorage(tmp_path).save_items([item1], "test-run")

    # Sem decisões de triagem prévias
    r = CliRunner().invoke(app, ["coding", "tui", "--run", "test-run", "--coder", "joao"])
    assert r.exit_code == 0
    assert "Nenhum item marcado como 'include'" in r.stdout

    # Adiciona decisão 'include' para liberar codificação
    append_decisions(
        tmp_path,
        "test-run",
        [
            ScreeningDecision(
                item_id="item_1",
                coder="joao",
                decision="include",
                criteria_met=["I1"],
                reason="OK",
                decided_at="2026-10-14T00:00:00Z",
                sheet_sha256="test",
            )
        ],
    )

    # Executa TUI via CLI com mock de CodingApp.run
    monkeypatch.setattr(CodingApp, "run", lambda self: None)
    r2 = CliRunner().invoke(app, ["coding-tui", "--run", "test-run", "--coder", "joao"])
    assert r2.exit_code == 0


def test_menu_screening_run_selector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from msrkit.cli.menu import _select_screening_run_id
    from msrkit.storage import ItemStorage

    monkeypatch.setattr("msrkit.cli.DATA_DIR", tmp_path)

    item1 = _make_item(1)
    ItemStorage(tmp_path).save_items([item1], "2026-10-14T01-00-00Z-test")
    ItemStorage(tmp_path).save_items([item1], "consolidated")

    # Escolha 1: Última coleta
    monkeypatch.setattr("rich.prompt.Prompt.ask", lambda *args, **kwargs: "1")
    sel1 = _select_screening_run_id()
    assert sel1 == "2026-10-14T01-00-00Z-test"

    # Escolha 2: Corpus Consolidado
    monkeypatch.setattr("rich.prompt.Prompt.ask", lambda *args, **kwargs: "2")
    sel2 = _select_screening_run_id()
    assert sel2 == "consolidated"
