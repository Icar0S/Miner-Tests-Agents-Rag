"""Textual TUI for interactive screening and literature review (Phase 2, T3/T4)."""

from __future__ import annotations

from contextlib import suppress
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
        elif event.key in ("escape", "q", "c", "0", "backspace"):
            self.dismiss(None)
        elif event.key in ("enter", "space"):
            pass
        else:
            with suppress(Exception):
                self.notify(
                    "Pressione de 1 a 6 para escolher o critério, ou [Esc] para cancelar.",
                    severity="information",
                )


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


def _format_item_body(it: Item, max_len: int | None = None) -> str:
    """Format item body for display, providing structured context for code artifacts."""
    if it.body:
        return it.body[:max_len] if max_len else it.body

    matched_str = (
        ", ".join(t.term if hasattr(t, "term") else str(t) for t in it.matched_terms)
        or "identificado no protocolo"
    )
    concepts_str = ", ".join(it.concepts) if it.concepts else "teste / RAG"

    if it.source == "github":
        from msrkit.enrich import repo_of

        rname = repo_of(it)
        tech = it.tech
        ci_str = "[green]✓ Sim (ativo em CI)[/green]" if tech.has_ci else "[dim]Não[/dim]"
        test_str = "[green]✓ Sim[/green]" if tech.has_tests else "[dim]Não[/dim]"
        maturidade = (
            f"{tech.contributors or 'N/A'} contribuidores | "
            f"{tech.active_months or 'N/A'} meses com commits"
        )
        lines = [
            "[bold cyan]📁 ARTEFATO DE CÓDIGO FONTE (GitHub)[/bold cyan]",
            f"• [bold]Repositório:[/bold] {rname or 'N/A'}",
            f"• [bold]Arquivo / Caminho:[/bold] [yellow]{tech.path or it.title or 'N/A'}[/yellow]",
            f"• [bold]Linguagem / Formato:[/bold] {tech.language or 'Configuração / YAML'}",
            f"• [bold]Sinais Técnicos:[/bold] CI/CD: {ci_str} | Suíte de Testes: {test_str}",
            f"• [bold]Maturidade:[/bold] {maturidade}",
            f"• [bold]Consulta de Origem:[/bold] [dim]{it.provenance.query_string}[/dim]",
            "",
            "[bold green]🔗 Inspecionar Código Online:[/bold green]",
            f"[underline]{it.url}[/underline]",
            "",
            "[dim]Nota Ética (§17 do Protocolo): O código-fonte integral de repositórios",
            "externos não é duplicado em disco para respeitar os direitos autorais.",
            "Para inspecionar o arquivo completo, acesse a URL acima no navegador.[/dim]",
        ]
        return "\n".join(lines)

    if it.source == "hackernews":
        lines = [
            "[bold yellow]💬 POST / DISCUSSÃO DA COMUNIDADE (Hacker News)[/bold yellow]",
            f"• [bold]Título:[/bold] {it.title or 'Sem título'}",
            f"• [bold]Autor:[/bold] @{it.author_handle or 'desconhecido'}",
            f"• [bold]Engajamento:[/bold] ★ {it.engagement.votes or 0} pontos | "
            f"💬 {it.engagement.comments or 0} comentários",
            f"• [bold]Conceitos do Protocolo (§3.1):[/bold] [cyan]{concepts_str}[/cyan]",
            f"• [bold]Termos Casados:[/bold] [yellow]{matched_str}[/yellow]",
            "",
            "[bold green]🔗 Recurso / Ferramenta Compartilhada (URL Externa):[/bold green]",
            f"[underline]{it.url}[/underline]",
            "",
            "[dim]Nota: Esta publicação no Hacker News é um link direto (link post) para",
            "o recurso, sem texto dissertativo na submissão. Para analisar a discussão",
            "ou o código, inspecione a URL acima no navegador.[/dim]",
        ]
        return "\n".join(lines)

    if it.source == "huggingface":
        tags_str = ", ".join(it.tech.tags[:8]) if it.tech.tags else "N/A"
        url_lower = str(it.url).lower()
        hf_type = (
            "Dataset"
            if "datasets" in url_lower
            else ("Space" if "spaces" in url_lower else "Modelo")
        )
        lines = [
            f"[bold magenta]🤗 ARTEFATO DE IA / {hf_type.upper()} (Hugging Face)[/bold magenta]",
            f"• [bold]Identificador:[/bold] {it.title or 'N/A'}",
            f"• [bold]Tipo de Artefato:[/bold] {hf_type}",
            f"• [bold]Tarefa / Pipeline:[/bold] {it.tech.language or 'Avaliação / RAG'}",
            f"• [bold]Engajamento / Impacto:[/bold] ★ {it.engagement.stars or 0} likes | "
            f"⤓ {it.engagement.views or 0} downloads",
            f"• [bold]Tags Técnicas:[/bold] [cyan]{tags_str}[/cyan]",
            f"• [bold]Termos Casados:[/bold] [yellow]{matched_str}[/yellow]",
            f"• [bold]Conceitos do Protocolo (§3.1):[/bold] [cyan]{concepts_str}[/cyan]",
            "",
            "[bold green]🔗 Inspecionar Model Card / Dataset no Hub:[/bold green]",
            f"[underline]{it.url}[/underline]",
            "",
            "[dim]Nota Técnica: A API de busca do Hugging Face expõe metadados e tags do artefato.",
            "O README.md completo do modelo pode ser consultado no Hub pelo link acima.[/dim]",
        ]
        return "\n".join(lines)

    if it.source in ("devto", "rss"):
        src_name = "dev.to" if it.source == "devto" else "Feed RSS / Medium"
        tags_str = ", ".join(it.tech.tags[:6]) if it.tech.tags else "N/A"
        lines = [
            f"[bold cyan]📰 ARTIGO TÉCNICO ({src_name})[/bold cyan]",
            f"• [bold]Título:[/bold] {it.title or 'N/A'}",
            f"• [bold]Autor:[/bold] @{it.author_handle or 'desconhecido'}",
            f"• [bold]Tópicos / Tags:[/bold] [cyan]{tags_str}[/cyan]",
            f"• [bold]Termos Casados:[/bold] [yellow]{matched_str}[/yellow]",
            f"• [bold]Conceitos do Protocolo (§3.1):[/bold] [cyan]{concepts_str}[/cyan]",
            "",
            "[bold green]🔗 Ler Artigo Completo Online:[/bold green]",
            f"[underline]{it.url}[/underline]",
            "",
            "[dim]Nota Ética (§17 do Protocolo): O texto integral de artigos de terceiros",
            "é mantido como metadata_only no pacote de replicação para respeitar direitos",
            "autorais. Acesse a URL acima para leitura integral.[/dim]",
        ]
        return "\n".join(lines)

    lines = [
        f"[bold]{it.title or 'Sem título'}[/bold]",
        f"• Fonte: {it.source} ({it.kind.value})",
        f"• Termos Casados: [yellow]{matched_str}[/yellow]",
        f"• URL: [underline]{it.url}[/underline]",
        "",
        "[dim](Sem corpo de texto adicional no pacote de metadados)[/dim]",
    ]
    return "\n".join(lines)


class ScreeningApp(App[None]):
    """Interactive full-screen TUI for screening grey literature items."""

    TITLE = "MSR-Kit — Triagem Interativa de Literatura Cinza"
    SUB_TITLE = (
        "[I] Incluir, [E] Excluir, [U] Dúvida, [S] Pular, [N/P] Páginas, [F] Fonte, [Q/Esc] Sair"
    )

    BINDINGS = [
        Binding("i", "include_item", "Incluir (I)", priority=True),
        Binding("e", "exclude_item", "Excluir (E)", priority=True),
        Binding("u", "uncertain_item", "Dúvida (U)", priority=True),
        Binding("s", "skip_item", "Pular (S)", priority=True),
        Binding("n", "next_page", "Próx Pág (N)", priority=True),
        Binding("p", "prev_page", "Pág Ant (P)", priority=True),
        Binding("f", "cycle_source", "Filtrar Fonte (F)", priority=True),
        Binding("q", "quit_app", "Sair e Salvar (Q)", priority=True),
        Binding("escape", "quit_app", "Voltar / Sair (Esc)", priority=True),
        Binding("pageup", "prev_page", "Pág Ant", show=False),
        Binding("pagedown", "next_page", "Próx Pág", show=False),
    ]

    DEFAULT_CSS = """
    Screen {
        layout: horizontal;
    }
    #sidebar {
        width: 34;
        border-right: heavy $primary;
        background: $panel;
    }
    .sidebar-title {
        text-style: bold;
        color: $accent;
        padding: 0 1;
    }
    .sidebar-subtitle {
        color: $text-muted;
        padding: 0 1;
        margin-bottom: 1;
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
        page_size: int = 50,
    ) -> None:
        super().__init__()
        self.all_items = items
        self.raw_items = items
        self.criteria = criteria
        self.data_dir = data_dir
        self.run_id = run_id
        self.coder = coder
        self.detections = detections or []
        self.ranking = rank_items(items, self.detections, criteria.batch_size)
        self.recorded_decisions: list[ScreeningDecision] = []
        self.decisions_map: dict[str, str] = {}
        self.page_size = max(1, page_size)
        self.current_page = 0
        self.current_page_idx = 0
        self.current_idx = 0
        self.source_filter: str | None = None

    @property
    def available_sources(self) -> list[str]:
        sources: set[str] = set()
        for it in self.all_items:
            if it.source:
                sources.add(it.source)
        return sorted(sources)

    @property
    def visible_items(self) -> list[Item]:
        if self.source_filter is None:
            return self.all_items
        return [it for it in self.all_items if it.source == self.source_filter]

    @property
    def total_pages(self) -> int:
        return max(1, (len(self.visible_items) + self.page_size - 1) // self.page_size)

    @property
    def current_page_items(self) -> list[Item]:
        visible = self.visible_items
        start = self.current_page * self.page_size
        end = min(start + self.page_size, len(visible))
        return visible[start:end]

    @property
    def current_item(self) -> Item | None:
        items = self.current_page_items
        if 0 <= self.current_page_idx < len(items):
            return items[self.current_page_idx]
        return None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Label(
                    "[bold]Itens da Fila[/bold]", id="sidebar-title", classes="sidebar-title"
                )
                yield Label(
                    "[dim]Pág 1/1[/dim]", id="sidebar-pagination", classes="sidebar-subtitle"
                )
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
                    yield Button(
                        "◀ Pág (P)", variant="default", id="btn-prev-page", classes="action-btn"
                    )
                    yield Button(
                        "Pág ▶ (N)", variant="default", id="btn-next-page", classes="action-btn"
                    )
                    yield Button(
                        "Fonte (F)", variant="primary", id="btn-cycle-source", classes="action-btn"
                    )
                    yield Button("Sair (Q)", variant="default", id="btn-quit", classes="action-btn")
        yield Footer()

    def on_mount(self) -> None:
        for btn in self.query(Button):
            btn.can_focus = False
        self._render_current_page(reset_selection=True)

    def _render_current_page(self, reset_selection: bool = True) -> None:
        list_view = self.query_one("#item-list", ListView)
        list_view.clear()

        visible = self.visible_items
        total = len(visible)
        total_p = self.total_pages
        if self.current_page >= total_p:
            self.current_page = max(0, total_p - 1)

        start_idx = self.current_page * self.page_size
        end_idx = min(start_idx + self.page_size, total)
        page_items = visible[start_idx:end_idx]

        for i, it in enumerate(page_items):
            mark = self.decisions_map.get(it.id)
            list_view.append(ItemListItem(it, start_idx + i + 1, decision=mark))

        src_label = (
            f"[bold cyan]{self.source_filter}[/bold cyan]"
            if self.source_filter
            else "[dim]Todas as fontes[/dim]"
        )
        sub_lbl = self.query_one("#sidebar-pagination", Label)
        sub_lbl.update(
            f"Pág [bold]{self.current_page + 1}/{total_p}[/bold] "
            f"({len(page_items)}/{total}) · {src_label}"
        )

        if reset_selection and page_items:
            self.current_page_idx = 0
            self.current_idx = start_idx
            list_view.index = 0
            self._display_item(0)
        elif not page_items:
            self._clear_display()

    def _clear_display(self) -> None:
        self.query_one("#item-title", Label).update(
            "[dim](Nenhum item nesta página ou filtro)[/dim]"
        )
        self.query_one("#item-meta", Label).update("")
        self.query_one("#item-tech", Label).update("")
        self.query_one("#item-evidence", Label).update("")
        self.query_one("#item-body", Static).update(
            "[dim]Use [F] para alternar fontes ou [P/N] para mudar de página.[/dim]"
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ItemListItem):
            list_view = self.query_one("#item-list", ListView)
            try:
                self.current_page_idx = list_view.children.index(event.item)
            except ValueError:
                self.current_page_idx = 0
            self.current_idx = self.current_page * self.page_size + self.current_page_idx
            self._display_item(self.current_page_idx)

    def _display_item(self, idx: int) -> None:
        page_items = self.current_page_items
        if not (0 <= idx < len(page_items)):
            return
        it = page_items[idx]
        self.current_idx = self.current_page * self.page_size + idx
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
                "[green]CI/CD: [✓ Sim][/green]" if it.tech.has_ci else "[dim]CI/CD: [✗ Não][/dim]"
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
        body_widget.update(_format_item_body(it))

    def action_include_item(self) -> None:
        it = self.current_item
        if not it:
            self.notify(
                "Nenhum item selecionado ou disponível nesta página.",
                severity="warning",
            )
            return
        now = datetime.now(UTC).isoformat()
        dec = ScreeningDecision(
            item_id=it.id,
            coder=self.coder,
            decision="include",
            criteria_met=list(self.criteria.inclusion.keys()) or ["I1"],
            reason="Incluído via TUI Textual",
            decided_at=now,
            sheet_sha256="tui_textual",
        )
        self._record_and_advance(it.id, dec, "include")

    def action_exclude_item(self) -> None:
        it = self.current_item
        if not it:
            self.notify(
                "Nenhum item selecionado ou disponível nesta página.",
                severity="warning",
            )
            return

        def handle_modal_result(crit: str | None) -> None:
            if not crit:
                return
            target_it = self.current_item
            if not target_it:
                return
            now = datetime.now(UTC).isoformat()
            dec = ScreeningDecision(
                item_id=target_it.id,
                coder=self.coder,
                decision="exclude",
                criteria_met=[crit],
                reason=f"Excluído por {crit}",
                decided_at=now,
                sheet_sha256="tui_textual",
            )
            self._record_and_advance(target_it.id, dec, "exclude")

        self.push_screen(ExclusionModal(), handle_modal_result)

    def action_uncertain_item(self) -> None:
        it = self.current_item
        if not it:
            self.notify(
                "Nenhum item selecionado ou disponível nesta página.",
                severity="warning",
            )
            return
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
        if not self.current_item:
            self.notify("Nenhum item para pular.", severity="information")
            return
        self._advance()

    def action_next_page(self) -> None:
        total_p = self.total_pages
        if self.current_page + 1 < total_p:
            self.current_page += 1
            self._render_current_page(reset_selection=True)
            self.notify(f"Página {self.current_page + 1} de {total_p}")
        else:
            self.notify(f"Você já está na última página ({total_p}/{total_p}).")

    def action_prev_page(self) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self._render_current_page(reset_selection=True)
            self.notify(f"Página {self.current_page + 1} de {self.total_pages}")
        else:
            self.notify("Você já está na primeira página (1/1).")

    def action_cycle_source(self) -> None:
        srcs = self.available_sources
        if not srcs:
            self.notify("Apenas uma fonte disponível nesta coleta.")
            return

        cycle_list: list[str | None] = [None] + list(srcs)
        if self.source_filter not in cycle_list:
            next_idx = 1
        else:
            curr_idx = cycle_list.index(self.source_filter)
            next_idx = (curr_idx + 1) % len(cycle_list)

        self.source_filter = cycle_list[next_idx]
        self.current_page = 0
        self.current_page_idx = 0
        self._render_current_page(reset_selection=True)

        active_name = self.source_filter or "Todas as fontes"
        count = len(self.visible_items)
        self.notify(f"Filtro de fonte: {active_name} ({count} itens)")

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
        if 0 <= self.current_page_idx < len(list_view.children):
            item_widget = list_view.children[self.current_page_idx]
            if isinstance(item_widget, ItemListItem):
                item_widget.decision = mark
                it = item_widget.item
                badge = "[✓]" if mark == "include" else ("[✗]" if mark == "exclude" else "[?]")
                label = item_widget.query_one(Label)
                label.update(f"{badge} #{item_widget.index} {(it.title or 'Sem título')[:28]}")
        self._advance()

    def _advance(self) -> None:
        page_items = self.current_page_items
        if self.current_page_idx + 1 < len(page_items):
            self.current_page_idx += 1
            self.current_idx = self.current_page * self.page_size + self.current_page_idx
            list_view = self.query_one("#item-list", ListView)
            list_view.index = self.current_page_idx
            self._display_item(self.current_page_idx)
        else:
            total_p = self.total_pages
            if self.current_page + 1 < total_p:
                self.current_page += 1
                self.notify(f"Avançando para página {self.current_page + 1} de {total_p}")
                self._render_current_page(reset_selection=True)
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
        elif btn_id == "btn-prev-page":
            self.action_prev_page()
        elif btn_id == "btn-next-page":
            self.action_next_page()
        elif btn_id == "btn-cycle-source":
            self.action_cycle_source()
        elif btn_id == "btn-quit":
            self.action_quit_app()


class CodingApp(App[None]):
    """Interactive full-screen TUI for qualitative data extraction (Annex A Coding)."""

    TITLE = "MSR-Kit — Extração Técnica de Dados (Annex A Coding)"
    SUB_TITLE = "[G] Gravar, [S] Pular, [N/P] Páginas, [F] Filtrar Fonte, [Q/Esc] Sair"

    BINDINGS = [
        Binding("g", "save_and_advance", "Gravar (G)"),
        Binding("s", "skip_item", "Pular (S)"),
        Binding("n", "next_page", "Próx Pág (N)"),
        Binding("p", "prev_page", "Pág Ant (P)"),
        Binding("f", "cycle_source", "Filtrar Fonte (F)"),
        Binding("q", "quit_app", "Sair e Salvar (Q)"),
        Binding("escape", "quit_app", "Voltar / Sair (Esc)", priority=True),
        Binding("pageup", "prev_page", "Pág Ant", show=False),
        Binding("pagedown", "next_page", "Próx Pág", show=False),
    ]

    DEFAULT_CSS = """
    Screen {
        layout: horizontal;
    }
    #coding-sidebar {
        width: 34;
        border-right: heavy $primary;
        background: $panel;
    }
    .sidebar-title {
        text-style: bold;
        color: $accent;
        padding: 0 1;
    }
    .sidebar-subtitle {
        color: $text-muted;
        padding: 0 1;
        margin-bottom: 1;
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
        page_size: int = 50,
    ) -> None:
        super().__init__()
        self.all_items = items
        self.raw_items = items
        self.data_dir = data_dir
        self.run_id = run_id
        self.coder = coder
        self.suggestions_map = suggestions_map or {}
        self.recorded_codings: list[CodingRecord] = []
        self.codings_map: dict[str, bool] = {}
        self.page_size = max(1, page_size)
        self.current_page = 0
        self.current_page_idx = 0
        self.current_idx = 0
        self.source_filter: str | None = None

        self.selected_sistema: list[str] = ["rag"]
        self.selected_nivel: str = "N1"
        self.selected_oraculo: str = "pseudo-automatico"
        self.selected_confianca: str = "alta"

    @property
    def available_sources(self) -> list[str]:
        sources: set[str] = set()
        for it in self.all_items:
            if it.source:
                sources.add(it.source)
        return sorted(sources)

    @property
    def visible_items(self) -> list[Item]:
        if self.source_filter is None:
            return self.all_items
        return [it for it in self.all_items if it.source == self.source_filter]

    @property
    def total_pages(self) -> int:
        return max(1, (len(self.visible_items) + self.page_size - 1) // self.page_size)

    @property
    def current_page_items(self) -> list[Item]:
        visible = self.visible_items
        start = self.current_page * self.page_size
        end = min(start + self.page_size, len(visible))
        return visible[start:end]

    @property
    def current_item(self) -> Item | None:
        items = self.current_page_items
        if 0 <= self.current_page_idx < len(items):
            return items[self.current_page_idx]
        return None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="coding-sidebar"):
                yield Label(
                    "[bold]Itens Elegíveis[/bold]",
                    id="coding-sidebar-title",
                    classes="sidebar-title",
                )
                yield Label(
                    "[dim]Pág 1/1[/dim]",
                    id="coding-sidebar-pagination",
                    classes="sidebar-subtitle",
                )
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
                        "Gravar (G)",
                        id="btn-save",
                        variant="success",
                        classes="action-btn",
                    )
                    yield Button(
                        "Pular (S)", id="btn-skip", variant="default", classes="action-btn"
                    )
                    yield Button(
                        "◀ Pág (P)", id="btn-prev-page", variant="default", classes="action-btn"
                    )
                    yield Button(
                        "Pág ▶ (N)", id="btn-next-page", variant="default", classes="action-btn"
                    )
                    yield Button(
                        "Fonte (F)", id="btn-cycle-source", variant="primary", classes="action-btn"
                    )
                    yield Button("Sair (Q)", id="btn-quit", variant="error", classes="action-btn")
        yield Footer()

    def on_mount(self) -> None:
        for btn in self.query(Button):
            btn.can_focus = False
        self._render_current_page(reset_selection=True)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "input-tools":
            self.query_one("#input-notes", Input).focus()
        elif event.input.id == "input-notes":
            self.action_save_and_advance()

    def _render_current_page(self, reset_selection: bool = True) -> None:
        list_view = self.query_one("#coding-item-list", ListView)
        list_view.clear()

        visible = self.visible_items
        total = len(visible)
        total_p = self.total_pages
        if self.current_page >= total_p:
            self.current_page = max(0, total_p - 1)

        start_idx = self.current_page * self.page_size
        end_idx = min(start_idx + self.page_size, total)
        page_items = visible[start_idx:end_idx]

        for i, it in enumerate(page_items):
            mark = "include" if self.codings_map.get(it.id) else None
            list_view.append(ItemListItem(it, start_idx + i + 1, decision=mark))

        src_label = (
            f"[bold cyan]{self.source_filter}[/bold cyan]"
            if self.source_filter
            else "[dim]Todas as fontes[/dim]"
        )
        sub_lbl = self.query_one("#coding-sidebar-pagination", Label)
        sub_lbl.update(
            f"Pág [bold]{self.current_page + 1}/{total_p}[/bold] "
            f"({len(page_items)}/{total}) · {src_label}"
        )

        if reset_selection and page_items:
            self.current_page_idx = 0
            self.current_idx = start_idx
            list_view.index = 0
            self._display_item(0)
        elif not page_items:
            self._clear_display()

    def _clear_display(self) -> None:
        self.query_one("#coding-title", Label).update(
            "[dim](Nenhum item elegível nesta página ou filtro)[/dim]"
        )
        self.query_one("#coding-meta", Label).update("")
        self.query_one("#coding-tech", Label).update("")
        self.query_one("#coding-suggestions", Label).update("")
        self.query_one("#coding-body", Static).update(
            "[dim]Use [F] para alternar fontes ou [P/N] para mudar de página.[/dim]"
        )

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, ItemListItem):
            list_view = self.query_one("#coding-item-list", ListView)
            try:
                self.current_page_idx = list_view.children.index(event.item)
            except ValueError:
                self.current_page_idx = 0
            self.current_idx = self.current_page * self.page_size + self.current_page_idx
            self._display_item(self.current_page_idx)

    def _display_item(self, idx: int) -> None:
        page_items = self.current_page_items
        if not (0 <= idx < len(page_items)):
            return
        it = page_items[idx]
        self.current_idx = self.current_page * self.page_size + idx
        author_str = f" por @{it.author_handle}" if it.author_handle else ""

        global_idx = self.current_page * self.page_size + idx + 1
        total_visible = len(self.visible_items)
        self.query_one("#coding-title", Label).update(
            f"[{global_idx}/{total_visible}] {it.title or 'Sem título'}"
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
                "[green]CI/CD: [✓ Sim][/green]" if it.tech.has_ci else "[dim]CI/CD: [✗ Não][/dim]"
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
            sug_text = "[bold yellow]Sugestões Automáticas:[/bold yellow] " + " | ".join(sug_parts)
            sug_lbl.update(sug_text)
        else:
            sug_lbl.update("")

        body_preview = _format_item_body(it, max_len=2000)
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
        it = self.current_item
        if not it:
            self.notify(
                "Nenhum item selecionado ou disponível para codificação.",
                severity="warning",
            )
            return
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
        if 0 <= self.current_page_idx < len(list_view.children):
            item_widget = list_view.children[self.current_page_idx]
            if isinstance(item_widget, ItemListItem):
                item_widget.decision = "include"
                badge = "[✓]"
                label = item_widget.query_one(Label)
                label.update(f"{badge} #{item_widget.index} {(it.title or 'Sem título')[:28]}")

        self._advance()

    def action_skip_item(self) -> None:
        if not self.current_item:
            self.notify("Nenhum item para pular.", severity="information")
            return
        self._advance()

    def action_next_page(self) -> None:
        total_p = self.total_pages
        if self.current_page + 1 < total_p:
            self.current_page += 1
            self._render_current_page(reset_selection=True)
            self.notify(f"Página {self.current_page + 1} de {total_p}")
        else:
            self.notify(f"Você já está na última página ({total_p}/{total_p}).")

    def action_prev_page(self) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self._render_current_page(reset_selection=True)
            self.notify(f"Página {self.current_page + 1} de {self.total_pages}")
        else:
            self.notify("Você já está na primeira página (1/1).")

    def action_cycle_source(self) -> None:
        srcs = self.available_sources
        if not srcs:
            self.notify("Apenas uma fonte disponível nesta coleta.")
            return

        cycle_list: list[str | None] = [None] + list(srcs)
        if self.source_filter not in cycle_list:
            next_idx = 1
        else:
            curr_idx = cycle_list.index(self.source_filter)
            next_idx = (curr_idx + 1) % len(cycle_list)

        self.source_filter = cycle_list[next_idx]
        self.current_page = 0
        self.current_page_idx = 0
        self._render_current_page(reset_selection=True)

        active_name = self.source_filter or "Todas as fontes"
        count = len(self.visible_items)
        self.notify(f"Filtro de fonte: {active_name} ({count} itens)")

    def action_quit_app(self) -> None:
        if isinstance(self.focused, Input):
            self.set_focus(None)
            self.notify(
                "Edição desativada. Pressione [Esc] ou [Q] novamente para sair.",
                severity="information",
            )
            return
        if self.recorded_codings:
            append_codings(self.data_dir, self.run_id, self.recorded_codings)
            try:
                from msrkit.cli.corpus import prisma

                prisma(run_id=self.run_id)
            except Exception:
                pass
        self.exit()

    def _advance(self) -> None:
        page_items = self.current_page_items
        if self.current_page_idx + 1 < len(page_items):
            self.current_page_idx += 1
            self.current_idx = self.current_page * self.page_size + self.current_page_idx
            list_view = self.query_one("#coding-item-list", ListView)
            list_view.index = self.current_page_idx
            self._display_item(self.current_page_idx)
        else:
            total_p = self.total_pages
            if self.current_page + 1 < total_p:
                self.current_page += 1
                self.notify(f"Avançando para página {self.current_page + 1} de {total_p}")
                self._render_current_page(reset_selection=True)
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
        elif btn_id == "btn-prev-page":
            self.action_prev_page()
        elif btn_id == "btn-next-page":
            self.action_next_page()
        elif btn_id == "btn-cycle-source":
            self.action_cycle_source()
        elif btn_id == "btn-quit":
            self.action_quit_app()
