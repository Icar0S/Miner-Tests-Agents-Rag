"""Textual TUI for interactive screening and literature review (Phase 2, T3/T4)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static

from msrkit.coding import CodingRecord, append_codings
from msrkit.screening import (
    ScreeningCriteria,
    ScreeningDecision,
    append_decisions,
    rank_items,
)

if TYPE_CHECKING:
    from pathlib import Path

    from msrkit.extract import Detection
    from msrkit.models import Item


class ExclusionModal(ModalScreen[str | None]):
    """Modal dialog to select the specific exclusion criterion (E1 to E6)."""

    DEFAULT_CSS = """
    ExclusionModal {
        align: center middle;
    }
    #modal-container {
        width: 65;
        height: auto;
        border: thick $error;
        background: $surface;
        padding: 1 2;
    }
    #modal-title {
        text-align: center;
        text-style: bold;
        color: $error;
        margin-bottom: 1;
    }
    .crit-btn {
        width: 100%;
        margin-bottom: 1;
    }
    """

    CRITERIA = [
        ("E1", "E1: Não é teste de RAG/Agente (LLM4Testing / outro software)"),
        ("E2", "E2: Apenas modelo isolado (sem recuperação ou ações)"),
        ("E3", "E3: Conteúdo promocional sem detalhe técnico verificável"),
        ("E4", "E4: Duplicata já analisada"),
        ("E5", "E5: Tutorial genérico sem oráculo ou métricas"),
        ("E6", "E6: Repositório sem sinais de manutenção (para N3)"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-container"):
            yield Label("Selecione o Critério de Exclusão", id="modal-title")
            for crit_id, label in self.CRITERIA:
                yield Button(label, id=f"btn-{crit_id}", classes="crit-btn")
            yield Button("Cancelar (Esc)", id="btn-cancel", variant="default", classes="crit-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id.startswith("btn-E"):
            crit = btn_id.replace("btn-", "")
            self.dismiss(crit)
        else:
            self.dismiss(None)

    def on_key(self, event: Any) -> None:
        if event.key in ("1", "2", "3", "4", "5", "6"):
            self.dismiss(f"E{event.key}")
        elif event.key == "escape":
            self.dismiss(None)


class ItemListItem(ListItem):
    """List item widget for an item being screened."""

    def __init__(self, item: Item, index: int, decision: str | None = None) -> None:
        super().__init__()
        self.item = item
        self.index = index
        self.decision = decision

    def compose(self) -> ComposeResult:
        badge = (
            "[✓]"
            if self.decision == "include"
            else ("[✗]" if self.decision == "exclude" else "[ ]")
        )
        title_snippet = (self.item.title or "Sem título")[:28]
        yield Label(f"{badge} #{self.index} {title_snippet}")


class ScreeningApp(App[None]):
    """Interactive full-screen TUI for screening grey literature items."""

    TITLE = "MSR-Kit — Triagem Interativa de Literatura Cinza"
    SUB_TITLE = "Pressione [I] Incluir, [E] Excluir, [U] Dúvida, [S] Pular, [Q] Sair"

    BINDINGS = [
        Binding("i", "include_item", "Incluir (I)", priority=True),
        Binding("e", "exclude_item", "Excluir (E)", priority=True),
        Binding("u", "uncertain_item", "Dúvida (U)", priority=True),
        Binding("s", "skip_item", "Pular (S)", priority=True),
        Binding("q", "quit_app", "Sair e Salvar (Q)", priority=True),
    ]

    DEFAULT_CSS = """
    Screen {
        layout: horizontal;
    }
    #sidebar {
        width: 32;
        border-right: heavy $primary;
        background: $panel;
    }
    #content-area {
        width: 1fr;
        padding: 1 2;
    }
    #item-header {
        border-bottom: solid $primary;
        margin-bottom: 1;
        padding-bottom: 1;
    }
    #item-title {
        text-style: bold;
        color: $accent;
    }
    #item-meta {
        color: $text-muted;
    }
    #item-body-scroll {
        height: 1fr;
        border: round $primary;
        padding: 1;
        background: $surface;
        margin-top: 1;
    }
    #action-bar {
        height: auto;
        margin-top: 1;
        layout: horizontal;
    }
    .action-btn {
        margin-right: 1;
    }
    """

    def __init__(
        self,
        items: list[Item],
        criteria: ScreeningCriteria,
        data_dir: Path,
        run_id: str,
        coder: str,
        detections: list[Detection] | None = None,
    ) -> None:
        super().__init__()
        self.raw_items = items
        self.criteria = criteria
        self.data_dir = data_dir
        self.run_id = run_id
        self.coder = coder
        self.detections = detections or []
        self.ranking = rank_items(items, self.detections, criteria.batch_size)
        self.recorded_decisions: list[ScreeningDecision] = []
        self.decisions_map: dict[str, str] = {}
        self.current_idx = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Label("[bold]Itens da Fila[/bold]", classes="sidebar-title")
                yield ListView(id="item-list")
            with Vertical(id="content-area"):
                with Vertical(id="item-header"):
                    yield Label("Carregando...", id="item-title")
                    yield Label("", id="item-meta")
                    yield Label("", id="item-tech")
                    yield Label("", id="item-evidence")
                with VerticalScroll(id="item-body-scroll"):
                    yield Static("", id="item-body")
                with Horizontal(id="action-bar"):
                    yield Button(
                        "Incluir (I)", variant="success", id="btn-include", classes="action-btn"
                    )
                    yield Button(
                        "Excluir (E)", variant="error", id="btn-exclude", classes="action-btn"
                    )
                    yield Button(
                        "Dúvida (U)", variant="warning", id="btn-uncertain", classes="action-btn"
                    )
                    yield Button(
                        "Pular (S)", variant="default", id="btn-skip", classes="action-btn"
                    )
                    yield Button("Sair (Q)", variant="default", id="btn-quit", classes="action-btn")
        yield Footer()

    def on_mount(self) -> None:
        list_view = self.query_one("#item-list", ListView)
        for idx, it in enumerate(self.raw_items, 1):
            list_view.append(ItemListItem(it, idx))
        if self.raw_items:
            list_view.index = 0
            self._display_item(0)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ItemListItem):
            self.current_idx = event.item.index - 1
            self._display_item(self.current_idx)

    def _display_item(self, idx: int) -> None:
        if not (0 <= idx < len(self.raw_items)):
            return
        it = self.raw_items[idx]
        rk = self.ranking.get(it.id)
        batch_str = f"Batch #{rk.batch} | Rank #{rk.rank} (Score: {rk.score:.1f})" if rk else ""

        title_lbl = self.query_one("#item-title", Label)
        title_lbl.update(f"{it.title or 'Sem título'} ({batch_str})")

        meta_lbl = self.query_one("#item-meta", Label)
        author_str = f" | Autor: {it.author_handle}" if it.author_handle else ""
        meta_lbl.update(
            f"Fonte: [bold]{it.source}[/bold] ({it.kind.value}){author_str} | URL: {it.url}"
        )

        tech_lbl = self.query_one("#item-tech", Label)
        tech_parts: list[str] = []
        if it.engagement.stars is not None:
            tech_parts.append(f"★ {it.engagement.stars} estrelas")
        if it.engagement.forks is not None:
            tech_parts.append(f"⑂ {it.engagement.forks} forks")
        if it.tech.has_ci is not None:
            ci_str = (
                "[green]CI/CD: [✓ Sim][/green]"
                if it.tech.has_ci
                else "[dim]CI/CD: [✗ Não][/dim]"
            )
            tech_parts.append(ci_str)
        if it.tech.has_tests is not None:
            tests_str = (
                "[green]Testes: [✓ Sim][/green]"
                if it.tech.has_tests
                else "[dim]Testes: [✗ Não][/dim]"
            )
            tech_parts.append(tests_str)
        if it.tech.contributors is not None:
            tech_parts.append(f"Contribuidores: {it.tech.contributors}")
        if it.tech.active_months is not None:
            tech_parts.append(f"Atividade: {it.tech.active_months}m")
        if it.tech.tags:
            tags_snippet = ", ".join(it.tech.tags[:5])
            tech_parts.append(f"Tags: [cyan]{tags_snippet}[/cyan]")

        if tech_parts:
            tech_lbl.update(" | ".join(tech_parts))
        else:
            tech_lbl.update("")

        ev_lbl = self.query_one("#item-evidence", Label)
        matched = (
            ", ".join(t.term if hasattr(t, "term") else str(t) for t in it.matched_terms)
            if it.matched_terms
            else "nenhum"
        )
        ev_lbl.update(f"Termos casados: [yellow]{matched}[/yellow]")

        body_widget = self.query_one("#item-body", Static)
        body_widget.update(it.body or "[dim](Sem corpo de texto disponível)[/dim]")

    def action_include_item(self) -> None:
        if not self.raw_items or self.current_idx >= len(self.raw_items):
            return
        it = self.raw_items[self.current_idx]
        now = datetime.now(UTC).isoformat()
        dec = ScreeningDecision(
            item_id=it.id,
            coder=self.coder,
            decision="include",
            criteria_met=["I1"],
            reason="Incluído via TUI Textual",
            decided_at=now,
            sheet_sha256="tui_textual",
        )
        self._record_and_advance(it.id, dec, "include")

    def action_exclude_item(self) -> None:
        if not self.raw_items or self.current_idx >= len(self.raw_items):
            return

        def handle_modal_result(crit: str | None) -> None:
            if not crit or self.current_idx >= len(self.raw_items):
                return
            it = self.raw_items[self.current_idx]
            now = datetime.now(UTC).isoformat()
            dec = ScreeningDecision(
                item_id=it.id,
                coder=self.coder,
                decision="exclude",
                criteria_met=[crit],
                reason=f"Excluído por {crit}",
                decided_at=now,
                sheet_sha256="tui_textual",
            )
            self._record_and_advance(it.id, dec, "exclude")

        self.push_screen(ExclusionModal(), handle_modal_result)

    def action_uncertain_item(self) -> None:
        if not self.raw_items or self.current_idx >= len(self.raw_items):
            return
        it = self.raw_items[self.current_idx]
        now = datetime.now(UTC).isoformat()
        dec = ScreeningDecision(
            item_id=it.id,
            coder=self.coder,
            decision="uncertain",
            criteria_met=[],
            reason="Dúvida via TUI Textual",
            decided_at=now,
            sheet_sha256="tui_textual",
        )
        self._record_and_advance(it.id, dec, "uncertain")

    def action_skip_item(self) -> None:
        if not self.raw_items or self.current_idx >= len(self.raw_items):
            return
        self._advance()

    def action_quit_app(self) -> None:
        if self.recorded_decisions:
            append_decisions(self.data_dir, self.run_id, self.recorded_decisions)
            try:
                from msrkit.cli.corpus import prisma

                prisma(run_id=self.run_id)
            except Exception:
                pass
        self.exit()

    def _record_and_advance(self, item_id: str, decision: ScreeningDecision, mark: str) -> None:
        self.recorded_decisions = [d for d in self.recorded_decisions if d.item_id != item_id]
        self.recorded_decisions.append(decision)
        self.decisions_map[item_id] = mark
        list_view = self.query_one("#item-list", ListView)
        if 0 <= self.current_idx < len(list_view.children):
            item_widget = list_view.children[self.current_idx]
            if isinstance(item_widget, ItemListItem):
                item_widget.decision = mark
                it = item_widget.item
                badge = "[✓]" if mark == "include" else ("[✗]" if mark == "exclude" else "[?]")
                label = item_widget.query_one(Label)
                label.update(f"{badge} #{item_widget.index} {(it.title or 'Sem título')[:28]}")
        self._advance()

    def _advance(self) -> None:
        self.current_idx += 1
        if self.current_idx < len(self.raw_items):
            list_view = self.query_one("#item-list", ListView)
            list_view.index = self.current_idx
            self._display_item(self.current_idx)
        else:
            self.notify("Fim dos itens desta sessão!")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-include":
            self.action_include_item()
        elif btn_id == "btn-exclude":
            self.action_exclude_item()
        elif btn_id == "btn-uncertain":
            self.action_uncertain_item()
        elif btn_id == "btn-skip":
            self.action_skip_item()
        elif btn_id == "btn-quit":
            self.action_quit_app()


class CodingApp(App[None]):
    """Interactive full-screen TUI for qualitative data extraction (Annex A Coding)."""

    TITLE = "MSR-Kit — Extração Técnica de Dados (Annex A Coding)"
    SUB_TITLE = (
        "Preencha as dimensões do protocolo e pressione [G / Enter] para Gravar, [Q] Sair"
    )

    BINDINGS = [
        Binding("g", "save_and_advance", "Gravar (G)", priority=True),
        Binding("s", "skip_item", "Pular (S)", priority=True),
        Binding("q", "quit_app", "Sair e Salvar (Q)", priority=True),
    ]

    DEFAULT_CSS = """
    Screen {
        layout: horizontal;
    }
    #coding-sidebar {
        width: 32;
        border-right: heavy $primary;
        background: $panel;
    }
    #coding-content-area {
        width: 1fr;
        padding: 1 2;
    }
    #coding-header {
        border-bottom: solid $primary;
        margin-bottom: 1;
        padding-bottom: 1;
    }
    #coding-title {
        text-style: bold;
        color: $accent;
    }
    #coding-meta {
        color: $text-muted;
    }
    #coding-tech {
        color: $success;
        margin-top: 1;
    }
    #coding-suggestions {
        color: $warning;
        margin-top: 1;
    }
    #coding-body-scroll {
        height: 8;
        border: round $primary;
        padding: 1;
        background: $surface;
        margin-top: 1;
        margin-bottom: 1;
    }
    #coding-form-scroll {
        height: 1fr;
        border: solid $secondary;
        padding: 1;
        background: $panel;
    }
    .form-row {
        height: auto;
        layout: horizontal;
        margin-bottom: 1;
        align-vertical: middle;
    }
    .form-label {
        width: 18;
        text-style: bold;
        color: $primary;
    }
    .option-btn {
        margin-right: 1;
    }
    .field-input {
        width: 1fr;
    }
    #coding-action-bar {
        height: auto;
        margin-top: 1;
        layout: horizontal;
    }
    .action-btn {
        margin-right: 1;
    }
    """

    def __init__(
        self,
        items: list[Item],
        data_dir: Path,
        run_id: str,
        coder: str,
        suggestions_map: dict[str, dict[str, str]] | None = None,
    ) -> None:
        super().__init__()
        self.raw_items = items
        self.data_dir = data_dir
        self.run_id = run_id
        self.coder = coder
        self.suggestions_map = suggestions_map or {}
        self.recorded_codings: list[CodingRecord] = []
        self.codings_map: dict[str, bool] = {}
        self.current_idx = 0

        self.selected_sistema: list[str] = ["rag"]
        self.selected_nivel: str = "N1"
        self.selected_oraculo: str = "pseudo-automatico"
        self.selected_confianca: str = "alta"

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="coding-sidebar"):
                yield Label("[bold]Itens Elegíveis[/bold]", classes="sidebar-title")
                with VerticalScroll():
                    yield ListView(id="coding-item-list")
            with Vertical(id="coding-content-area"):
                with Vertical(id="coding-header"):
                    yield Label("Carregando...", id="coding-title")
                    yield Label("", id="coding-meta")
                    yield Label("", id="coding-tech")
                    yield Label("", id="coding-suggestions")
                with VerticalScroll(id="coding-body-scroll"):
                    yield Static("", id="coding-body")
                with VerticalScroll(id="coding-form-scroll"):
                    with Horizontal(classes="form-row"):
                        yield Label("Sistema:", classes="form-label")
                        yield Button(
                            "RAG", id="btn-sys-rag", classes="option-btn", variant="primary"
                        )
                        yield Button("Agente", id="btn-sys-agent", classes="option-btn")
                        yield Button("Ambos (RAG+Agente)", id="btn-sys-both", classes="option-btn")
                    with Horizontal(classes="form-row"):
                        yield Label("Nível Evidência:", classes="form-label")
                        yield Button(
                            "N1 (Texto)", id="btn-lvl-n1", classes="option-btn", variant="primary"
                        )
                        yield Button("N2 (Snippet)", id="btn-lvl-n2", classes="option-btn")
                        yield Button("N3 (Código/CI)", id="btn-lvl-n3", classes="option-btn")
                    with Horizontal(classes="form-row"):
                        yield Label("Tipo de Oráculo:", classes="form-label")
                        yield Button(
                            "LLM-as-Judge",
                            id="btn-ora-pseudo",
                            classes="option-btn",
                            variant="primary",
                        )
                        yield Button("Referência/Golden", id="btn-ora-ref", classes="option-btn")
                        yield Button("Asserts (Espec)", id="btn-ora-spec", classes="option-btn")
                        yield Button("Derivado (Metam)", id="btn-ora-der", classes="option-btn")
                        yield Button("Humano", id="btn-ora-hum", classes="option-btn")
                    with Horizontal(classes="form-row"):
                        yield Label("Confiança:", classes="form-label")
                        yield Button(
                            "Alta", id="btn-conf-alta", classes="option-btn", variant="primary"
                        )
                        yield Button("Média", id="btn-conf-media", classes="option-btn")
                        yield Button("Baixa", id="btn-conf-baixa", classes="option-btn")
                    with Horizontal(classes="form-row"):
                        yield Label("Ferramentas:", classes="form-label")
                        yield Input(
                            id="input-tools",
                            classes="field-input",
                            placeholder="ex: ragas, deepeval, pytest",
                        )
                    with Horizontal(classes="form-row"):
                        yield Label("Anotações:", classes="form-label")
                        yield Input(
                            id="input-notes",
                            classes="field-input",
                            placeholder="Notas qualitativas do avaliador",
                        )
                with Horizontal(id="coding-action-bar"):
                    yield Button(
                        "Gravar e Avançar (G)",
                        id="btn-save",
                        variant="success",
                        classes="action-btn",
                    )
                    yield Button(
                        "Pular (S)", id="btn-skip", variant="default", classes="action-btn"
                    )
                    yield Button(
                        "Sair e Salvar (Q)", id="btn-quit", variant="error", classes="action-btn"
                    )
        yield Footer()

    def on_mount(self) -> None:
        list_view = self.query_one("#coding-item-list", ListView)
        for idx, it in enumerate(self.raw_items, 1):
            list_view.append(ItemListItem(it, idx, decision=None))
        if self.raw_items:
            self._display_item(0)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ItemListItem):
            self.current_idx = event.item.index - 1
            self._display_item(self.current_idx)

    def _display_item(self, idx: int) -> None:
        if not (0 <= idx < len(self.raw_items)):
            return
        it = self.raw_items[idx]
        author_str = f" por @{it.author_handle}" if it.author_handle else ""

        self.query_one("#coding-title", Label).update(
            f"[{idx + 1}/{len(self.raw_items)}] {it.title or 'Sem título'}"
        )
        self.query_one("#coding-meta", Label).update(
            f"Fonte: [bold]{it.source}[/bold] ({it.kind.value}){author_str} | URL: {it.url}"
        )

        tech_lbl = self.query_one("#coding-tech", Label)
        tech_parts: list[str] = []
        if it.engagement.stars is not None:
            tech_parts.append(f"★ {it.engagement.stars} estrelas")
        if it.engagement.forks is not None:
            tech_parts.append(f"⑂ {it.engagement.forks} forks")
        if it.tech.has_ci is not None:
            ci_str = (
                "[green]CI/CD: [✓ Sim][/green]"
                if it.tech.has_ci
                else "[dim]CI/CD: [✗ Não][/dim]"
            )
            tech_parts.append(ci_str)
        if it.tech.has_tests is not None:
            tests_str = (
                "[green]Testes: [✓ Sim][/green]"
                if it.tech.has_tests
                else "[dim]Testes: [✗ Não][/dim]"
            )
            tech_parts.append(tests_str)
        if it.tech.contributors is not None:
            tech_parts.append(f"Contribuidores: {it.tech.contributors}")
        if it.tech.tags:
            tech_parts.append(f"Tags: [cyan]{', '.join(it.tech.tags[:5])}[/cyan]")
        tech_lbl.update(" | ".join(tech_parts) if tech_parts else "")

        sug = self.suggestions_map.get(it.id, {})
        sug_parts: list[str] = []
        if sug.get("auto_sistema"):
            sug_parts.append(f"Sistema: [bold]{sug['auto_sistema']}[/bold]")
        if sug.get("auto_nivel"):
            sug_parts.append(f"Nível: [bold]{sug['auto_nivel']}[/bold]")
        if sug.get("auto_ferramentas"):
            sug_parts.append(f"Ferramentas: [bold]{sug['auto_ferramentas']}[/bold]")
        sug_lbl = self.query_one("#coding-suggestions", Label)
        if sug_parts:
            sug_text = (
                "[bold yellow]Sugestões Automáticas:[/bold yellow] " + " | ".join(sug_parts)
            )
            sug_lbl.update(sug_text)
        else:
            sug_lbl.update("")

        body_preview = it.body[:2000] if it.body else "[dim]Sem corpo de texto disponível.[/dim]"
        self.query_one("#coding-body", Static).update(body_preview)

        if sug.get("auto_sistema"):
            s_val = sug["auto_sistema"].lower()
            if "rag" in s_val and "agente" in s_val:
                self.selected_sistema = ["rag", "agente"]
            elif "agente" in s_val:
                self.selected_sistema = ["agente"]
            else:
                self.selected_sistema = ["rag"]
        else:
            self.selected_sistema = ["rag"]

        if sug.get("auto_nivel") in ("N1", "N2", "N3"):
            self.selected_nivel = sug["auto_nivel"]
        else:
            self.selected_nivel = "N1"

        tools_input = self.query_one("#input-tools", Input)
        tools_input.value = sug.get("auto_ferramentas", "")

        notes_input = self.query_one("#input-notes", Input)
        notes_input.value = ""

        self._refresh_button_variants()

    def _refresh_button_variants(self) -> None:
        is_both = "rag" in self.selected_sistema and "agente" in self.selected_sistema
        is_rag = "rag" in self.selected_sistema and not is_both
        is_agent = "agente" in self.selected_sistema and not is_both

        self.query_one("#btn-sys-rag", Button).variant = "primary" if is_rag else "default"
        self.query_one("#btn-sys-agent", Button).variant = "primary" if is_agent else "default"
        self.query_one("#btn-sys-both", Button).variant = "primary" if is_both else "default"

        self.query_one("#btn-lvl-n1", Button).variant = (
            "primary" if self.selected_nivel == "N1" else "default"
        )
        self.query_one("#btn-lvl-n2", Button).variant = (
            "primary" if self.selected_nivel == "N2" else "default"
        )
        self.query_one("#btn-lvl-n3", Button).variant = (
            "primary" if self.selected_nivel == "N3" else "default"
        )

        self.query_one("#btn-ora-pseudo", Button).variant = (
            "primary" if self.selected_oraculo == "pseudo-automatico" else "default"
        )
        self.query_one("#btn-ora-ref", Button).variant = (
            "primary" if self.selected_oraculo == "referencia" else "default"
        )
        self.query_one("#btn-ora-spec", Button).variant = (
            "primary" if self.selected_oraculo == "especificado" else "default"
        )
        self.query_one("#btn-ora-der", Button).variant = (
            "primary" if self.selected_oraculo == "derivado" else "default"
        )
        self.query_one("#btn-ora-hum", Button).variant = (
            "primary" if self.selected_oraculo == "humano" else "default"
        )

        self.query_one("#btn-conf-alta", Button).variant = (
            "primary" if self.selected_confianca == "alta" else "default"
        )
        self.query_one("#btn-conf-media", Button).variant = (
            "primary" if self.selected_confianca == "media" else "default"
        )
        self.query_one("#btn-conf-baixa", Button).variant = (
            "primary" if self.selected_confianca == "baixa" else "default"
        )

    def action_save_and_advance(self) -> None:
        if not self.raw_items or self.current_idx >= len(self.raw_items):
            return
        it = self.raw_items[self.current_idx]
        tools_val = self.query_one("#input-tools", Input).value.strip()
        notes_val = self.query_one("#input-notes", Input).value.strip()

        vals: dict[str, Any] = {
            "sistema": self.selected_sistema,
            "nivel_evidencia": self.selected_nivel,
            "tipo_oraculo": self.selected_oraculo,
            "confianca": self.selected_confianca,
            "evidencia_textual": (it.body or it.title or "")[:280],
        }
        if tools_val:
            seen_tools: set[str] = set()
            cleaned_tools: list[str] = []
            for t in tools_val.split(","):
                cleaned = t.strip()
                if cleaned and cleaned.lower() not in seen_tools:
                    seen_tools.add(cleaned.lower())
                    cleaned_tools.append(cleaned)
            vals["ferramentas"] = cleaned_tools

        rec = CodingRecord(
            item_id=it.id,
            coder=self.coder,
            values=vals,
            notes=notes_val,
            coded_at=datetime.now(UTC).isoformat(),
            sheet_sha256="tui_coding",
        )
        self.recorded_codings = [c for c in self.recorded_codings if c.item_id != it.id]
        self.recorded_codings.append(rec)
        self.codings_map[it.id] = True

        list_view = self.query_one("#coding-item-list", ListView)
        if 0 <= self.current_idx < len(list_view.children):
            item_widget = list_view.children[self.current_idx]
            if isinstance(item_widget, ItemListItem):
                item_widget.decision = "include"
                badge = "[✓]"
                label = item_widget.query_one(Label)
                label.update(f"{badge} #{item_widget.index} {(it.title or 'Sem título')[:28]}")

        self._advance()

    def action_skip_item(self) -> None:
        self._advance()

    def action_quit_app(self) -> None:
        if self.recorded_codings:
            append_codings(self.data_dir, self.run_id, self.recorded_codings)
            try:
                from msrkit.cli.corpus import prisma

                prisma(run_id=self.run_id)
            except Exception:
                pass
        self.exit()

    def _advance(self) -> None:
        self.current_idx += 1
        if self.current_idx < len(self.raw_items):
            list_view = self.query_one("#coding-item-list", ListView)
            list_view.index = self.current_idx
            self._display_item(self.current_idx)
        else:
            self.notify("Fim dos itens para codificação!")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-sys-rag":
            self.selected_sistema = ["rag"]
            self._refresh_button_variants()
        elif btn_id == "btn-sys-agent":
            self.selected_sistema = ["agente"]
            self._refresh_button_variants()
        elif btn_id == "btn-sys-both":
            self.selected_sistema = ["rag", "agente"]
            self._refresh_button_variants()
        elif btn_id == "btn-lvl-n1":
            self.selected_nivel = "N1"
            self._refresh_button_variants()
        elif btn_id == "btn-lvl-n2":
            self.selected_nivel = "N2"
            self._refresh_button_variants()
        elif btn_id == "btn-lvl-n3":
            self.selected_nivel = "N3"
            self._refresh_button_variants()
        elif btn_id == "btn-ora-pseudo":
            self.selected_oraculo = "pseudo-automatico"
            self._refresh_button_variants()
        elif btn_id == "btn-ora-ref":
            self.selected_oraculo = "referencia"
            self._refresh_button_variants()
        elif btn_id == "btn-ora-spec":
            self.selected_oraculo = "especificado"
            self._refresh_button_variants()
        elif btn_id == "btn-ora-der":
            self.selected_oraculo = "derivado"
            self._refresh_button_variants()
        elif btn_id == "btn-ora-hum":
            self.selected_oraculo = "humano"
            self._refresh_button_variants()
        elif btn_id == "btn-conf-alta":
            self.selected_confianca = "alta"
            self._refresh_button_variants()
        elif btn_id == "btn-conf-media":
            self.selected_confianca = "media"
            self._refresh_button_variants()
        elif btn_id == "btn-conf-baixa":
            self.selected_confianca = "baixa"
            self._refresh_button_variants()
        elif btn_id == "btn-save":
            self.action_save_and_advance()
        elif btn_id == "btn-skip":
            self.action_skip_item()
        elif btn_id == "btn-quit":
            self.action_quit_app()
