"""Textual TUI for interactive screening and literature review (Phase 2, T3/T4)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, Static

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
        badge = "[✓]" if self.decision == "include" else ("[✗]" if self.decision == "exclude" else "[ ]")
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
                    yield Label("", id="item-evidence")
                with VerticalScroll(id="item-body-scroll"):
                    yield Static("", id="item-body")
                with Horizontal(id="action-bar"):
                    yield Button("Incluir (I)", variant="success", id="btn-include", classes="action-btn")
                    yield Button("Excluir (E)", variant="error", id="btn-exclude", classes="action-btn")
                    yield Button("Dúvida (U)", variant="warning", id="btn-uncertain", classes="action-btn")
                    yield Button("Pular (S)", variant="default", id="btn-skip", classes="action-btn")
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
        meta_lbl.update(f"Fonte: [bold]{it.source}[/bold] ({it.kind.value}){author_str} | URL: {it.url}")

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
        if not self.raw_items:
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
        if not self.raw_items:
            return

        def handle_modal_result(crit: str | None) -> None:
            if not crit:
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
        if not self.raw_items:
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
        self._advance()

    def action_quit_app(self) -> None:
        if self.recorded_decisions:
            append_decisions(self.data_dir, self.run_id, self.recorded_decisions)
        self.exit()

    def _record_and_advance(self, item_id: str, decision: ScreeningDecision, mark: str) -> None:
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
        if self.current_idx + 1 < len(self.raw_items):
            self.current_idx += 1
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
