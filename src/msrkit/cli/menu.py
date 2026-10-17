"""Interactive menu."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from msrkit.adapters.base import BaseAdapter

from rich.prompt import Prompt
from rich.table import Table

from msrkit import __version__
from msrkit import cli as _cli
from msrkit.cli._common import (
    DEFAULT_PROTOCOL,
    _get_latest_run_id,
    _get_registry,
    app,
    console,
    is_valid_coder_name,
)
from msrkit.cli.collect import run
from msrkit.cli.corpus import dedupe, export

CANCEL_TOKENS: tuple[str, ...] = ("0", "v", "q", "voltar", "sair", "exit", "cancelar")


def _safe_ask(
    prompt: str,
    default: str = "",
    choices: list[str] | None = None,
    show_default: bool = True,
) -> str:
    """Prompt user safely, returning default or '0' on KeyboardInterrupt/EOFError."""
    try:
        val = Prompt.ask(prompt, default=default, choices=choices, show_default=show_default)
        return val.strip()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Operação cancelada pelo usuário.[/yellow]")
        return "0"


def _safe_ask_choice(
    prompt: str,
    valid_choices: list[str],
    default: str | None = None,
) -> str | None:
    """Prompt user until a valid choice is entered or a cancel token is provided."""
    while True:
        try:
            val = Prompt.ask(
                prompt,
                default=default if default is not None else "",
                show_default=default is not None,
            ).strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Operação cancelada pelo usuário.[/yellow]")
            return None

        if not val and default is not None:
            val = default

        if val.lower() in CANCEL_TOKENS:
            return None

        if val in valid_choices:
            return val

        choices_str = ", ".join(valid_choices)
        console.print(
            f"[red]Opção inválida '{val}'! Escolha entre {choices_str} (ou 0 para voltar).[/red]"
        )


def _safe_ask_yes_no(
    prompt: str,
    default: bool = True,
) -> bool | None:
    """Prompt user for a yes/no confirmation with loop resilience."""
    suffix = "[S/n]" if default else "[s/N]"
    full_prompt = f"{prompt} {suffix}" if "[" not in prompt else prompt
    default_str = "s" if default else "n"

    while True:
        try:
            raw = Prompt.ask(full_prompt, default=default_str, show_default=False).strip().lower()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Operação cancelada pelo usuário.[/yellow]")
            return None

        if not raw:
            raw = default_str

        if raw in CANCEL_TOKENS:
            return None

        if raw in ("s", "sim", "y", "yes"):
            return True
        if raw in ("n", "nao", "não", "no"):
            return False

        console.print(
            "[red]Resposta inválida! Digite 's' para sim, 'n' para não (ou 0 para cancelar).[/red]"
        )


def _safe_ask_coder(
    prompt: str = "[bold cyan]Nome do avaliador (coder) (ou 0 para cancelar)[/bold cyan]",
    default: str = "revisor",
) -> str | None:
    """Prompt and validate a coder identifier safe for filenames and inter-coder records."""
    while True:
        try:
            raw = Prompt.ask(prompt, default=default, show_default=True).strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Operação cancelada pelo usuário.[/yellow]")
            return None

        if not raw:
            raw = default

        if raw.lower() in CANCEL_TOKENS:
            return None

        is_valid, reason = is_valid_coder_name(raw)
        if is_valid:
            return raw

        console.print(f"[red]Nome de avaliador inválido: {reason}[/red]")
        console.print(
            "[dim]Dica: use de 1 a 50 caracteres (letras, números, _ ou -), "
            "sem espaços, barras ou símbolos de pontuação.[/dim]"
        )


def _toggle_source_in_protocol(protocol_path: str, source_name: str, new_state: bool) -> bool:
    """Toggle a source's enabled state in the protocol YAML file while preserving comments."""
    import re

    p = Path(protocol_path)
    if not p.exists():
        return False
    try:
        content = p.read_text(encoding="utf-8")
        pattern = (
            rf"(^\s*{re.escape(source_name)}:\s*\n"
            r"(?:[ \t]*#[^\n]*\n)*[ \t]*enabled:\s*)(true|false)"
        )
        match = re.search(pattern, content, flags=re.MULTILINE)
        if match:
            new_val = "true" if new_state else "false"
            content = re.sub(pattern, rf"\g<1>{new_val}", content, count=1, flags=re.MULTILINE)
            p.write_text(content, encoding="utf-8")
            return True

        import yaml

        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if "sources" in data and source_name in data["sources"]:
            data["sources"][source_name]["enabled"] = new_state
            with p.open("w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
            return True
    except Exception as e:
        logging.getLogger(__name__).warning("Failed to update protocol file: %s", e)
    return False


def _get_source_display_info(
    s_name: str, is_enabled: bool, registry: dict[str, type[BaseAdapter]]
) -> tuple[str, str, str, str]:
    """Return (group, status_label, api_label, rationale) for a source."""
    if s_name not in registry:
        return (
            "no_api",
            "[dim]○ Desconhecida[/dim]",
            "[dim][Sem Adaptador][/dim]",
            "Fonte não registrada no MSR-Kit.",
        )

    adapter_cls = registry[s_name]
    policy = adapter_cls.policy
    avail = adapter_cls().available()

    if is_enabled:
        group = "active"
        status_label = "[bold green]● Ativada[/bold green]"
        if avail.status == "OK":
            api_label = "[bold green][API Oficial: OK][/bold green]"
        elif avail.status == "DEGRADED":
            api_label = "[bold yellow][API Oficial: Parcial/Faltam Chaves][/bold yellow]"
        else:
            api_label = "[bold red][API Oficial: Indisponível][/bold red]"
        rationale = policy.notes or "Fonte primária ativa no protocolo E2 (§6.1)."
    elif policy.has_official_api:
        group = "official_disabled"
        status_label = "[dim]○ Desativada[/dim]"
        if policy.exclusion_category == "methodological":
            api_label = (
                "[cyan][API Oficial: Excluída por Metodologia (Microblog sem Código)][/cyan]"
            )
        elif policy.exclusion_category == "access_gated":
            api_label = "[yellow][API Oficial: Desativada (Exige Aprovação Reddit nov/25)][/yellow]"
        elif policy.exclusion_category == "commercial_paywall":
            api_label = (
                "[yellow][API Oficial: Desativada (API Paga Pay-per-use)][/yellow]"
            )
        else:
            api_label = "[cyan][API Oficial: Desativada no Protocolo][/cyan]"
        rationale = policy.exclusion_rationale or policy.notes or "Desativada no protocolo."
    else:
        group = "no_api"
        status_label = "[dim]○ Desativada[/dim]"
        if policy.exclusion_category == "no_public_api":
            api_label = "[red][Sem API Pública (Termos Vedam Scraping)][/red]"
        elif policy.exclusion_category == "private_enclave":
            api_label = "[red][Sem Busca Aberta (Chat Privado / Requer Ética)][/red]"
        else:
            api_label = "[red][Sem API Pública][/red]"
        rationale = (
            policy.exclusion_rationale or policy.notes or "Sem suporte a API pública de busca."
        )

    return (group, status_label, api_label, rationale)


def _manage_sources_menu(protocol_path: str) -> None:  # pragma: no cover
    """Interactive screen to toggle sources on/off based on availability."""
    from msrkit.config import load_protocol

    while True:
        try:
            config = load_protocol(protocol_path)
        except Exception as e:
            console.print(f"[red]Erro ao carregar protocolo: {e}[/red]")
            return

        registry = _get_registry()
        all_sources = sorted(set(list(config.sources.keys()) + list(registry.keys())))

        table = Table(
            title="[bold cyan]Gerenciamento de Fontes de Pesquisa (§6.1 Protocolo E2)[/bold cyan]",
            border_style="cyan",
            header_style="bold magenta",
        )
        table.add_column("#", style="bold cyan", width=4)
        table.add_column("Fonte", style="bold", width=14)
        table.add_column("No Protocolo", justify="center", width=15)
        table.add_column("API Oficial", justify="center", width=26)
        table.add_column("Status / Justificativa Científica (§6.1)")

        source_list: list[tuple[str, bool]] = []
        for idx, s_name in enumerate(all_sources, 1):
            is_enabled = config.sources[s_name].enabled if s_name in config.sources else False
            source_list.append((s_name, is_enabled))

            grp, status_str, _, rationale = _get_source_display_info(s_name, is_enabled, registry)
            if s_name in registry:
                pol = registry[s_name].policy
                av = registry[s_name]().available()
                if pol.has_official_api:
                    if av.status == "OK":
                        api_str = "[bold green]✓ API Oficial (OK)[/bold green]"
                    elif av.status == "DEGRADED":
                        api_str = "[bold yellow]⚠ API Oficial (Parcial)[/bold yellow]"
                    elif pol.exclusion_category == "methodological":
                        api_str = "[bold cyan]✓ API Oficial (Excluída)[/bold cyan]"
                    elif pol.exclusion_category == "access_gated":
                        api_str = "[bold yellow]✓ API Oficial (Aprovação)[/bold yellow]"
                    elif pol.exclusion_category == "commercial_paywall":
                        api_str = "[bold yellow]✓ API Oficial (Paga)[/bold yellow]"
                    else:
                        api_str = "[bold yellow]✓ API Oficial[/bold yellow]"
                else:
                    if pol.exclusion_category == "no_public_api":
                        api_str = "[bold red]✗ Sem API Pública[/bold red]"
                    elif pol.exclusion_category == "private_enclave":
                        api_str = "[bold red]✗ Sem Busca Aberta[/bold red]"
                    else:
                        api_str = "[bold red]✗ Sem API Oficial[/bold red]"
            else:
                api_str = "[dim]Desconhecida[/dim]"

            table.add_row(str(idx), s_name, status_str, api_str, rationale)

        console.print()
        console.print(table)
        console.print(
            "[dim]• Digite o número da fonte para alternar (Ativar ⇄ Desativar no protocolo).[/dim]"
        )
        console.print(
            "[dim]• Fontes '✓ API Oficial (OK)' estão ativas e prontas para mineração.[/dim]"
        )
        console.print(
            "[dim]• Fontes '✓ API Oficial (Excluída/Paga)' possuem API oficial mas foram\n"
            "  desativadas por critérios metodológicos ou de custo comercial (§6.1).[/dim]"
        )
        console.print(
            "[dim]• Fontes '✗ Sem API Pública' não expõem busca aberta (auditoria PRISMA).[/dim]"
        )

        choice = _safe_ask(
            "\n[bold green]Digite o número da fonte para alternar (ou 0 para voltar)[/bold green]",
            default="0",
        )
        if choice in ("0", "v", "q", "voltar", "sair", "exit", "cancelar"):
            break

        try:
            chosen_idx = int(choice)
            if 1 <= chosen_idx <= len(source_list):
                target_source, curr_state = source_list[chosen_idx - 1]
                target_policy = (
                    registry[target_source].policy if target_source in registry else None
                )
                new_state = not curr_state
                if target_policy and not target_policy.has_official_api and new_state:
                    console.print(
                        f"\n[bold red]✗ A fonte '{target_source}' não pode ser ativada:[/bold red] "
                        "[red]não possui API pública de busca e scraping é proibido (§6.1 C).[/red]"
                    )
                    continue
                if (
                    target_policy
                    and target_policy.exclusion_category == "methodological"
                    and new_state
                ):
                    console.print(
                        f"\n[bold yellow]⚠ Aviso Metodológico:[/bold yellow] "
                        f"[yellow]'{target_source}' foi excluída no protocolo E2 (§6.1) por ser "
                        "microblog sem código. Ativá-la alterará o escopo do estudo.[/yellow]"
                    )
                success = _toggle_source_in_protocol(protocol_path, target_source, new_state)
                if success:
                    word = (
                        "[bold green]ativada[/bold green]"
                        if new_state
                        else "[yellow]desativada[/yellow]"
                    )
                    console.print(
                        f"\n[green]✓ Fonte[/green] [bold]{target_source}[/bold] {word} "
                        "[green]com sucesso no protocolo![/green]"
                    )
                else:
                    console.print(
                        f"\n[red]✗ Não foi possível alterar a fonte '{target_source}'.[/red]"
                    )
            else:
                console.print("[red]Número inválido![/red]")
        except ValueError:
            console.print("[red]Entrada inválida! Digite um número.[/red]")


def _interactive_mining_menu(protocol_path: str) -> None:  # pragma: no cover
    """Interactive mining execution with custom quantity and source selection."""
    from msrkit.config import load_protocol

    try:
        config = load_protocol(protocol_path)
    except Exception as e:
        console.print(f"[red]Erro ao carregar protocolo: {e}[/red]")
        return

    registry = _get_registry()

    console.print("\n[bold cyan]─── 1. Escolha a Fonte para Minerar ───[/bold cyan]")
    console.print("  [cyan]0[/cyan] - ↩ [bold]Voltar ao Menu Principal (Cancelar)[/bold]")
    active_sources = [s for s, c in sorted(config.sources.items()) if c.enabled]
    console.print(
        f"  [bold cyan]T[/bold cyan] - 🌐 "
        f"[bold]Todas as {len(active_sources)} fontes ativadas[/bold] "
        f"[dim]({', '.join(active_sources)})[/dim]"
    )

    sources_options: list[str] = []
    all_s_names = sorted(config.sources.keys())

    group_items: dict[str, list[str]] = {
        "active": [],
        "official_disabled": [],
        "no_api": [],
    }

    for s_name in all_s_names:
        is_en = config.sources[s_name].enabled if s_name in config.sources else False
        grp, _, _, _ = _get_source_display_info(s_name, is_en, registry)
        if grp not in group_items:
            group_items["no_api"].append(s_name)
        else:
            group_items[grp].append(s_name)

    # Grupo 1: Ativadas no Protocolo
    if group_items["active"]:
        console.print(
            "\n  [bold green]● Fontes Ativadas no Protocolo (Prontas para Coleta):[/bold green]"
        )
        for s_name in group_items["active"]:
            sources_options.append(s_name)
            idx = len(sources_options)
            _, status_label, api_label, _ = _get_source_display_info(s_name, True, registry)
            console.print(
                f"    [cyan]{idx:<2}[/cyan] - {s_name:<14} {status_label:<20} {api_label}"
            )

    # Grupo 2: Têm API Oficial, mas Desativadas Metodologicamente ou por Barreira
    if group_items["official_disabled"]:
        console.print(
            "\n  [bold yellow]○ Desativadas no Protocolo (Metodologia/Política):[/bold yellow]"
        )
        for s_name in group_items["official_disabled"]:
            sources_options.append(s_name)
            idx = len(sources_options)
            _, status_label, api_label, _ = _get_source_display_info(s_name, False, registry)
            console.print(
                f"    [cyan]{idx:<2}[/cyan] - {s_name:<14} {status_label:<20} {api_label}"
            )

    # Grupo 3: Desativadas por Ausência de API Pública ou Natureza Fechada
    if group_items["no_api"]:
        console.print(
            "\n  [bold red]○ Desativadas por Ausência de API Aberta ou Natureza Fechada:[/bold red]"
        )
        for s_name in group_items["no_api"]:
            sources_options.append(s_name)
            idx = len(sources_options)
            _, status_label, api_label, _ = _get_source_display_info(s_name, False, registry)
            console.print(
                f"    [cyan]{idx:<2}[/cyan] - {s_name:<14} {status_label:<20} {api_label}"
            )

    selected_source: str | None = None
    while True:
        src_choice = _safe_ask(
            "\n[bold green]Escolha o número da fonte, T para todas ou 0 para voltar[/bold green]",
            default="0",
        )
        if src_choice in ("0", "v", "q", "voltar", "sair", "exit", "cancelar"):
            console.print("[yellow]Operação cancelada. Retornando ao menu principal.[/yellow]")
            return

        if src_choice.lower() in ("t", "todas", "all"):
            selected_source = None
            break

        try:
            s_idx = int(src_choice)
            if 1 <= s_idx <= len(sources_options):
                selected_source = sources_options[s_idx - 1]
                if selected_source in registry:
                    sel_policy = registry[selected_source].policy
                    if not sel_policy.has_official_api:
                        desc = sel_policy.exclusion_rationale or "Scraping é proibido."
                        err_text = (
                            f"\n[bold red]⚠ '{selected_source}' não tem API pública.[/bold red]\n"
                            f"[dim]{desc}[/dim]\n"
                            "[yellow]Mantida apenas como registro formal PRISMA.[/yellow]"
                        )
                        console.print(err_text)
                        continue
                    elif not (
                        config.sources.get(selected_source)
                        and config.sources[selected_source].enabled
                    ):
                        api_n = sel_policy.official_api_name or "Sim"
                        motivo = sel_policy.exclusion_rationale or sel_policy.notes
                        console.print(
                            f"\n[bold yellow]ℹ '{selected_source}' tem API ({api_n}), "
                            "mas está desativada no E2.[/bold yellow]\n"
                            f"[cyan]Motivo:[/cyan] {motivo}\n"
                        )
                        prompt_msg = (
                            "[bold cyan]Forçar coleta experimental desta fonte?[/bold cyan]"
                        )
                        confirm = _safe_ask_yes_no(prompt_msg, default=False)
                        if not confirm:
                            console.print(
                                "[yellow]Seleção cancelada. Escolha outra fonte.[/yellow]"
                            )
                            continue
                break
            else:
                console.print(
                    f"[red]Número inválido! Digite um valor entre 1 e {len(sources_options)}, "
                    "'T' para todas ou '0' para voltar.[/red]"
                )
        except ValueError:
            console.print(
                "[red]Entrada inválida! Digite o número da fonte, 'T' para todas ou '0' "
                "para voltar.[/red]"
            )

    console.print("\n[bold cyan]─── 2. Escolha a Quantidade de Itens para Minerar ───[/bold cyan]")
    console.print("  [cyan]1[/cyan] - ⚡ Teste Rápido (5 itens)")
    console.print("  [cyan]2[/cyan] - 🔍 Amostra Pequena (20 itens)")
    console.print("  [cyan]3[/cyan] - 📊 Amostra Média (50 itens)")
    console.print("  [cyan]4[/cyan] - 🚀 Coleta Ampla (200 itens)")
    console.print("  [cyan]5[/cyan] - ♾️  Máximo do Protocolo (sem limite rápido)")
    console.print("  [cyan]6[/cyan] - ✏️  Digitar quantidade personalizada")
    console.print("  [cyan]0[/cyan] - ↩ Cancelar e voltar ao Menu Principal")

    limit: int | None = 20
    while True:
        qty_choice = _safe_ask(
            "\n[bold green]Escolha a opção de quantidade (ou 0 para cancelar)[/bold green]",
            default="2",
        )
        if qty_choice in ("0", "v", "q", "voltar", "sair", "exit", "cancelar"):
            console.print("[yellow]Mineração cancelada. Retornando ao menu principal.[/yellow]")
            return

        if qty_choice == "1":
            limit = 5
            break
        elif qty_choice == "2":
            limit = 20
            break
        elif qty_choice == "3":
            limit = 50
            break
        elif qty_choice == "4":
            limit = 200
            break
        elif qty_choice == "5":
            limit = None
            break
        elif qty_choice == "6":
            while True:
                custom = _safe_ask(
                    "[bold green]Digite a quantidade exata desejada "
                    "(ou 0 para cancelar)[/bold green]",
                    default="20",
                )
                if custom in ("0", "v", "q", "voltar", "sair", "exit", "cancelar"):
                    console.print(
                        "[yellow]Mineração cancelada. Retornando ao menu principal.[/yellow]"
                    )
                    return
                try:
                    custom_int = int(custom)
                    if custom_int > 0:
                        limit = custom_int
                        break
                    console.print(
                        "[red]A quantidade deve ser um número inteiro maior que zero.[/red]"
                    )
                except ValueError:
                    console.print(
                        "[red]Entrada inválida! Digite um número inteiro maior que zero.[/red]"
                    )
            break
        else:
            console.print(
                "[red]Opção de quantidade inválida! Digite de 1 a 6 ou 0 para voltar.[/red]"
            )

    src_label = selected_source if selected_source else "Todas as fontes ativadas"
    limit_label = str(limit) if limit is not None else "Ilimitado (máximo do protocolo)"
    console.print(
        f"\n[bold green]Iniciando Mineração:[/bold green] "
        f"Fonte: [bold cyan]{src_label}[/bold cyan] | "
        f"Limite: [bold cyan]{limit_label}[/bold cyan]\n"
    )

    try:
        run(protocol=protocol_path, source=selected_source, limit=limit, verbose=False)
    except Exception as e:
        console.print(f"[red]Erro durante a coleta: {e}[/red]")
        return

    latest_run = _get_latest_run_id()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_csv = f"data/resultados_{timestamp}_brutos.csv"

    console.print("\n[bold cyan]─── 3. Gerando Planilha Preliminar dos Achados ───[/bold cyan]")
    export(
        run_id=latest_run,
        fmt="csv",
        include_body=False,
        output=raw_csv,
        raw=True,
        verbose=False,
    )

    console.print(
        f"[bold green]✓ Planilha de achados brutos gerada:[/bold green] "
        f"[bold cyan]{raw_csv}[/bold cyan]"
    )

    console.print()
    do_dedupe = _safe_ask_yes_no(
        "[bold cyan]Deseja executar a desduplicação agora e gerar também "
        "a planilha desduplicada?[/bold cyan]",
        default=True,
    )
    if do_dedupe:
        console.print("\n[bold]1. Desduplicando itens coletados...[/bold]")
        dedupe(run_id=latest_run, verbose=False)
        dedup_csv = f"data/resultados_{timestamp}_desduplicados.csv"
        console.print("\n[bold]2. Exportando planilha desduplicada para CSV...[/bold]")
        export(
            run_id=latest_run,
            fmt="csv",
            include_body=False,
            output=dedup_csv,
            raw=False,
            verbose=False,
        )
        console.print(
            f"\n[bold green]✓ Planilha desduplicada gerada:[/bold green] "
            f"[bold cyan]{dedup_csv}[/bold cyan]"
        )
    else:
        console.print(
            "[dim]Desduplicação não executada agora. "
            "Todos os achados brutos continuam preservados.[/dim]"
        )
        console.print(
            "[dim]Dica: Você pode desduplicar a qualquer momento nas Opções Avançadas.[/dim]"
        )

    console.print("\n[bold cyan]─── 4. Extração Léxica & Ranqueamento ───[/bold cyan]")
    try:
        from msrkit.cli.evidence import extract

        extract(run_id=latest_run, protocol=protocol_path, top=10, verbose=False)
        console.print("[bold green]✓ Termos casados e fila ordenada por relevância![/bold green]")
    except Exception as e:
        console.print(f"[dim](Nota na extração automática: {e})[/dim]")

    console.print(
        f"\n[bold green]✓ Coleta e preparação concluídas para a rodada {latest_run}![/bold green]"
    )
    go_screen = _safe_ask_yes_no(
        "\n[bold cyan]Deseja iniciar a Triagem Interativa (TUI) agora?[/bold cyan]",
        default=True,
    )
    if go_screen:
        coder = _safe_ask_coder(
            "[bold cyan]Nome do avaliador (coder) (ou 0 para cancelar)[/bold cyan]",
            default="revisor",
        )
        if coder:
            from msrkit.cli.review import screen_tui

            screen_tui(
                run_id=latest_run,
                protocol=protocol_path,
                coder=coder,
                batch=None,
                limit=None,
            )


def _select_screening_run_id() -> str | None:
    """Select the target dataset for screening or coding (latest run, consolidated, or specific)."""
    import msrkit.cli as _cli
    from msrkit.storage import ItemStorage

    storage = ItemStorage(_cli.DATA_DIR)
    latest_run = _get_latest_run_id()
    latest_count = len(storage.read_items(latest_run, prefer_deduped=True)) if latest_run else 0
    consolidated_count = len(storage.read_items("consolidated", prefer_deduped=True))

    if not latest_run and consolidated_count == 0:
        console.print("\n[yellow]⚠ Nenhuma coleta minerada encontrada na pasta data/.[/yellow]")
        return None

    while True:
        console.print("\n[bold cyan]─── Escolha a Base de Dados ───[/bold cyan]")
        if latest_run:
            console.print(
                f"  [cyan]1[/cyan] - ⚡ Última coleta minerada "
                f"([bold]{latest_run}[/bold] · {latest_count} itens)"
            )
        else:
            console.print("  [dim]1 - ⚡ Última coleta minerada (nenhuma encontrada)[/dim]")

        console.print(
            f"  [cyan]2[/cyan] - 📚 Corpus Consolidado "
            f"([dim]todas as coletas acumuladas[/dim] · {consolidated_count} itens)"
        )
        console.print("  [cyan]3[/cyan] - 🔍 Escolher uma coleta específica do histórico")
        console.print("  [cyan]0[/cyan] - ↩ Voltar ao Menu Principal")

        target_choice = _safe_ask(
            "\n[bold green]Escolha a base desejada (ou 0 para voltar)[/bold green]",
            default="1" if latest_run else "2",
        )

        if target_choice in ("0", "v", "q", "voltar", "sair", "exit", "cancelar"):
            return None

        if target_choice == "1":
            if not latest_run:
                console.print(
                    "[yellow]Nenhuma coleta recente encontrada. Escolha outra opção.[/yellow]"
                )
                continue
            return latest_run
        elif target_choice == "2":
            return "consolidated"
        elif target_choice == "3":
            runs = [r for r in storage.list_runs() if r != "consolidated"]
            if not runs:
                console.print("[yellow]Nenhuma outra coleta encontrada no histórico.[/yellow]")
                return latest_run
            console.print("\n[bold cyan]Coletas Disponíveis:[/bold cyan]")
            for idx, r in enumerate(runs, 1):
                count = len(storage.read_items(r, prefer_deduped=True))
                console.print(f"  [cyan]{idx}[/cyan] - {r} ({count} itens)")
            console.print("  [cyan]0[/cyan] - ↩ Cancelar e voltar")
            while True:
                idx_choice = _safe_ask(
                    "\n[bold green]Digite o número da coleta (ou 0 para voltar)[/bold green]",
                    default="1",
                )
                if idx_choice in ("0", "v", "q", "voltar", "sair", "exit", "cancelar"):
                    break
                try:
                    chosen_idx = int(idx_choice)
                    if 1 <= chosen_idx <= len(runs):
                        return runs[chosen_idx - 1]
                    console.print(
                        f"[red]Número inválido! Escolha entre 1 e {len(runs)} "
                        "ou 0 para voltar.[/red]"
                    )
                except ValueError:
                    console.print("[red]Entrada inválida! Digite o número da coleta.[/red]")
        else:
            console.print("[red]Opção inválida! Escolha 1, 2, 3 ou 0 para voltar.[/red]")


def _results_menu(protocol_path: str) -> None:  # pragma: no cover
    """Submenu for PRISMA 2020 flow, statistical analysis, and reports."""
    from msrkit.cli.corpus import export, prisma, stats

    while True:
        console.print("\n[bold cyan]─── 📊 Resultados da Pesquisa & PRISMA 2020 ───[/bold cyan]")
        console.print(
            "  [cyan]1[/cyan] - [bold]Gerar / Atualizar Fluxo PRISMA 2020[/bold] "
            "(prisma.tikz e prisma.md)"
        )
        console.print(
            "  [cyan]2[/cyan] - [bold]Estatísticas da coleta / Panorama histórico[/bold] (stats)"
        )
        console.print(
            "  [cyan]3[/cyan] - [bold]Exportar dados para planilha CSV ou DuckDB[/bold] (export)"
        )
        console.print(
            "  [cyan]4[/cyan] - [bold]Análises estatísticas do artigo (RQ1-RQ3 / A1-A6)[/bold] "
            "(analyze)"
        )
        console.print("  [cyan]0[/cyan] - ↩ Voltar ao Menu Principal")

        choice = _safe_ask(
            "\n[bold green]Escolha uma opção de resultados[/bold green]", default="0"
        )
        if choice in ("0", "v", "q", "voltar", "sair", "exit", "cancelar"):
            break
        if choice == "1":
            selected_run = _select_screening_run_id()
            if not selected_run:
                continue
            prisma(run_id=selected_run)
        elif choice == "2":
            selected_run = _select_screening_run_id()
            if not selected_run:
                continue
            stats(
                run_id=selected_run,
                all_runs=(selected_run == "consolidated"),
                verbose=False,
            )
        elif choice == "3":
            selected_run = _select_screening_run_id()
            if not selected_run:
                continue
            is_all = selected_run == "consolidated"
            kind_choice = _safe_ask_choice(
                "Deseja exportar [1] Versão desduplicada, [2] Versão bruta (ou 0 para voltar)",
                valid_choices=["1", "2"],
                default="1",
            )
            if kind_choice is None:
                continue
            is_raw = kind_choice == "2"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix_type = "brutos" if is_raw else "desduplicados"
            suffix_scope = "consolidado" if is_all else f"coleta_{selected_run}"
            out_file = str(
                _cli.DATA_DIR / f"resultados_{timestamp}_{suffix_scope}_{suffix_type}.csv"
            )
            export(
                run_id=selected_run if not is_all else None,
                all_runs=is_all,
                fmt="csv",
                include_body=False,
                output=out_file,
                raw=is_raw,
                verbose=False,
            )
            console.print(
                f"[bold green]✓ Planilha salva em:[/bold green] [bold cyan]{out_file}[/bold cyan]"
            )
        elif choice == "4":
            selected_run = _select_screening_run_id()
            if not selected_run:
                continue
            _statistical_analyses_menu(selected_run, protocol_path)
        else:
            console.print("[red]Opção inválida! Escolha entre 0 e 4.[/red]")


def _statistical_analyses_menu(run_id: str | None, protocol_path: str) -> None:  # pragma: no cover
    """Submenu for statistical analyses A1-A6 supporting RQ1-RQ5."""
    from msrkit.cli.analyze import (
        analyze_compare,
        analyze_coverage,
        analyze_frequency,
        analyze_oracles,
        analyze_topics,
    )

    while True:
        console.print("\n[bold cyan]─── 📈 Análises Estatísticas do Estudo (A1–A6) ───[/bold cyan]")
        console.print(f"[dim]Base de dados ativa: {run_id}[/dim]\n")
        console.print(
            "  [bold cyan]1[/bold cyan] - 🚀 [bold]Todas as Análises em Lote (A1–A6)[/bold]"
        )
        console.print("  [cyan]2[/cyan] - A1/A2: Frequência de Ferramentas & Métodos (RQ1/RQ3)")
        console.print("  [cyan]3[/cyan] - A3: Cobertura de Modos de Falha (RQ4)")
        console.print("  [cyan]4[/cyan] - A4: Escada de Oráculos & Teste H2 (RQ2)")
        console.print("  [cyan]5[/cyan] - A6: Comparação RAG vs Agentes & Saturação (RQ5)")
        console.print("  [cyan]6[/cyan] - A5: Descoberta de Tópicos e Clusters (LDA / k-means)")
        console.print("  [cyan]0[/cyan] - ↩ Voltar")

        sub = _safe_ask_choice(
            "\n[bold green]Escolha uma opção de análise[/bold green]",
            valid_choices=["1", "2", "3", "4", "5", "6"],
            default="1",
        )
        if sub is None:
            break

        if sub in ("1", "2"):
            console.print("\n[bold cyan]Executando Análise de Frequência (A1/A2)...[/bold cyan]")
            try:
                analyze_frequency(
                    run_id=run_id,
                    basis="detections",
                    pairs="tool-method",
                    protocol=protocol_path,
                )
            except Exception as e:
                console.print(f"[yellow]Nota em A1/A2: {e}[/yellow]")

        if sub in ("1", "3"):
            console.print("\n[bold cyan]Executando Cobertura de Falhas (A3)...[/bold cyan]")
            try:
                analyze_coverage(run_id=run_id, protocol=protocol_path)
            except Exception as e:
                console.print(
                    f"[yellow]Nota em A3: {e} (requer formulário Anexo A codificado)[/yellow]"
                )

        if sub in ("1", "4"):
            console.print("\n[bold cyan]Executando Escada de Oráculos (A4)...[/bold cyan]")
            try:
                analyze_oracles(run_id=run_id, basis="detections", protocol=protocol_path)
            except Exception as e:
                console.print(f"[yellow]Nota em A4: {e}[/yellow]")

        if sub in ("1", "5"):
            console.print("\n[bold cyan]Executando Comparação RAG vs Agentes (A6)...[/bold cyan]")
            try:
                analyze_compare(run_id=run_id, basis="detections", protocol=protocol_path)
            except Exception as e:
                console.print(f"[yellow]Nota em A6: {e}[/yellow]")

        if sub in ("1", "6"):
            console.print("\n[bold cyan]Executando Descoberta de Tópicos (A5)...[/bold cyan]")
            try:
                analyze_topics(run_id=run_id, protocol=protocol_path, status="all")
            except (Exception, SystemExit):
                console.print(
                    "[yellow]Nota em A5: descoberta de tópicos requer 'scikit-learn' "
                    '(pip install -e ".[analysis]").[/yellow]'
                )

        if sub == "1":
            console.print(
                "\n[bold green]✓ Bateria de análises concluída! CSVs em data/reports/[/bold green]"
            )
            break
        elif sub not in ("1", "2", "3", "4", "5", "6"):
            console.print("[red]Opção inválida! Escolha entre 1 e 6, ou 0 para voltar.[/red]")


def _advanced_menu(protocol_path: str) -> None:  # pragma: no cover
    """Submenu for advanced tools: sources, CSV sheets, agreement, audit & Zenodo."""
    from msrkit.cli.collect import plan, sources, validate
    from msrkit.cli.corpus import dedupe, package
    from msrkit.cli.evidence import enrich, extract

    while True:
        console.print("\n[bold cyan]─── 🛠️  Opções Avançadas & Auditoria ───[/bold cyan]")
        console.print("\n[bold]Diagnóstico e Fontes:[/bold]")
        console.print("  [cyan]1[/cyan] - Gerenciar fontes e chaves de API (ativar/desativar)")
        console.print("  [cyan]2[/cyan] - Status detalhado das fontes e políticas (sources)")
        console.print("  [cyan]3[/cyan] - Simulação de planejamento / Dry-Run (plan)")
        console.print("  [cyan]4[/cyan] - Validar arquivo YAML de protocolo (validate)")

        console.print("\n[bold]Processamento e Planilhas Excel / CSV:[/bold]")
        console.print("  [cyan]5[/cyan] - Desduplicar itens coletados manualmente (dedupe)")
        console.print("  [cyan]6[/cyan] - Enriquecer repositórios GitHub com CI e testes (enrich)")
        console.print("  [cyan]7[/cyan] - Executar extração léxica do gazetteer (extract)")
        console.print("  [cyan]8[/cyan] - Exportar / Importar triagem em planilha CSV (screen)")
        console.print("  [cyan]9[/cyan] - Exportar / Importar Anexo A em planilha CSV (coding)")

        console.print("\n[bold]Auditoria Metodológica e Publicação:[/bold]")
        console.print(
            "  [cyan]10[/cyan] - Concordância entre avaliadores: Cohen's Kappa (§15) (agreement)"
        )
        console.print(
            "  [cyan]11[/cyan] - Validar busca contra o Gold Set de referência (§10) (recall)"
        )
        console.print(
            "  [cyan]12[/cyan] - Avaliar precisão da extração em amostra (§12) (precision)"
        )
        console.print("  [cyan]13[/cyan] - Gerar pacote reprodutível para o Zenodo (package)")
        console.print("\n  [cyan]0[/cyan] - ↩ Voltar ao Menu Principal")

        choice = _safe_ask("\n[bold green]Escolha uma opção avançada[/bold green]", default="0")
        if choice in ("0", "v", "q", "voltar", "sair", "exit", "cancelar"):
            break
        if choice == "1":
            _manage_sources_menu(protocol_path)
        elif choice == "2":
            sources(md=False, verbose=False)
        elif choice == "3":
            plan(protocol=protocol_path, source=None, verbose=False)
        elif choice == "4":
            validate(protocol=protocol_path, verbose=False)
        elif choice == "5":
            which = _safe_ask_choice(
                "Desduplicar [1] Apenas última coleta, [2] Histórico (ou 0 para voltar)",
                valid_choices=["1", "2"],
                default="1",
            )
            if which is None:
                continue
            dedupe(run_id=None, all_runs=(which == "2"), verbose=False)
        elif choice == "6":
            selected_run = _select_screening_run_id()
            if not selected_run:
                continue
            enrich(
                run_id=selected_run,
                limit=50,
                protocol=protocol_path,
                graphql=False,
                verbose=False,
            )
        elif choice == "7":
            selected_run = _select_screening_run_id()
            if not selected_run:
                continue
            extract(run_id=selected_run, protocol=protocol_path, top=25, verbose=False)
        elif choice == "8":
            sub_choice = _safe_ask_choice(
                "Deseja [1] Exportar CSV, [2] Importar CSV preenchido (ou 0 para voltar)?",
                valid_choices=["1", "2"],
                default="1",
            )
            if sub_choice is None:
                continue
            from msrkit.cli.review import screen_export, screen_import

            if sub_choice == "1":
                selected_run = _select_screening_run_id()
                if not selected_run:
                    continue
                coder = _safe_ask_coder(
                    "Nome do avaliador (ou 0 para voltar)", default="revisor"
                )
                if coder is None:
                    continue
                screen_export(
                    run_id=selected_run,
                    protocol=protocol_path,
                    coder=coder,
                    output=None,
                    pending_only=True,
                    order="relevance",
                    batch=[],
                )
            else:
                selected_run = _select_screening_run_id()
                if not selected_run:
                    continue
                sheet_file = _safe_ask(
                    "Caminho do arquivo CSV preenchido (ou 0 para voltar)"
                )
                if sheet_file in ("0", "v", "q", "voltar", "sair", "exit", "cancelar", ""):
                    continue
                screen_import(
                    sheet=sheet_file,
                    run_id=selected_run,
                    protocol=protocol_path,
                    coder="",
                )
        elif choice == "9":
            sub_choice = _safe_ask_choice(
                "Deseja [1] Exportar Anexo A CSV, [2] Importar CSV (ou 0 para voltar)?",
                valid_choices=["1", "2"],
                default="1",
            )
            if sub_choice is None:
                continue
            from msrkit.cli.review import coding_export, coding_import

            if sub_choice == "1":
                selected_run = _select_screening_run_id()
                if not selected_run:
                    continue
                coder = _safe_ask_coder(
                    "Nome do avaliador (ou 0 para voltar)", default="revisor"
                )
                if coder is None:
                    continue
                coding_export(
                    run_id=selected_run,
                    protocol=protocol_path,
                    coder=coder,
                    output=None,
                    status="include",
                    prefill=False,
                    pending_only=True,
                    double_sample=False,
                )
            else:
                selected_run = _select_screening_run_id()
                if not selected_run:
                    continue
                sheet_file = _safe_ask(
                    "Caminho do arquivo CSV preenchido (ou 0 para voltar)"
                )
                if sheet_file in ("0", "v", "q", "voltar", "sair", "exit", "cancelar", ""):
                    continue
                coding_import(
                    sheet=sheet_file,
                    run_id=selected_run,
                    protocol=protocol_path,
                    coder="",
                )
        elif choice == "10":
            selected_run = _select_screening_run_id()
            if not selected_run:
                continue
            sub_choice = _safe_ask_choice(
                "Deseja [1] Calcular índice Kappa e exportar divergências, "
                "[2] Aplicar resolução de divergências (disagreements.csv) (ou 0 para voltar)?",
                valid_choices=["1", "2"],
                default="1",
            )
            if sub_choice is None:
                continue
            if sub_choice == "1":
                from msrkit.cli.review import agreement

                agreement(run_id=selected_run, protocol=protocol_path, all_fields=False)
            else:
                from msrkit.cli.review import resolve_disagreements_cmd

                sheet_file = _safe_ask(
                    "Caminho do arquivo disagreements.csv (Enter para padrão ou 0 para voltar)",
                    default=f"data/coding/{selected_run}/disagreements.csv",
                )
                if sheet_file in ("0", "v", "q", "voltar", "sair", "exit", "cancelar"):
                    continue
                resolve_disagreements_cmd(sheet=sheet_file, run_id=selected_run)
        elif choice == "11":
            selected_run = _select_screening_run_id()
            if not selected_run:
                continue
            default_gold = (
                "protocols/gold_set.yaml"
                if Path("protocols/gold_set.yaml").exists()
                else "protocols/gold_set.example.yaml"
            )
            gold_file = _safe_ask(
                "Arquivo do Gold Set YAML (ou 0 para voltar)", default=default_gold
            )
            if gold_file in ("0", "v", "q", "voltar", "sair", "exit", "cancelar"):
                continue
            from msrkit.cli.validation import recall

            recall(
                gold=gold_file,
                run_id=selected_run,
                all_runs=(selected_run == "consolidated"),
            )
        elif choice == "12":
            selected_run = _select_screening_run_id()
            if not selected_run:
                continue
            sub_choice = _safe_ask_choice(
                "Deseja [1] Gerar amostra estratificada (.csv), "
                "[2] Calcular precisão de planilha avaliada (ou 0 para voltar)?",
                valid_choices=["1", "2"],
                default="1",
            )
            if sub_choice is None:
                continue
            if sub_choice == "1":
                from msrkit.cli.validation import precision_sample_cmd

                precision_sample_cmd(run_id=selected_run, n=50, seed=20260928)
            else:
                sheet_file = _safe_ask(
                    "Caminho do arquivo CSV avaliado (ou 0 para voltar)"
                )
                if sheet_file in ("0", "v", "q", "voltar", "sair", "exit", "cancelar", ""):
                    continue
                from msrkit.cli.validation import precision_score_cmd

                precision_score_cmd(sheet=sheet_file, run_id=selected_run)
        elif choice == "13":
            selected_run = _select_screening_run_id()
            if not selected_run:
                continue
            creator_name = _safe_ask(
                "Nome do autor/criador para Zenodo (ex: Lima, Matheus) (ou 0 para voltar)",
                default="",
            )
            if creator_name in ("0", "v", "q", "voltar", "sair", "exit", "cancelar"):
                continue
            creators = [creator_name] if creator_name else []
            package(
                run_id=selected_run,
                protocol=protocol_path,
                output=None,
                gold=None,
                creator=creators,
                keep_authors=False,
            )
        else:
            console.print("[red]Opção inválida! Escolha entre 0 e 13.[/red]")


@app.command(name="menu")
def menu() -> None:  # pragma: no cover
    """Interactive terminal menu to navigate MSR-Kit easily."""
    from rich.panel import Panel

    try:
        while True:
            console.print()
            console.print(
                Panel.fit(
                    f"[bold cyan]MSR-Kit[/bold cyan] [dim]v{__version__}[/dim]\n"
                    "[italic]Mining grey literature through official APIs[/italic]",
                    border_style="cyan",
                )
            )
            console.print("\n[bold]Fluxo Principal da Pesquisa:[/bold]")
            console.print(
                "  [bold cyan]1[/bold cyan] - [bold]🎯 1. Minerar & Preparar[/bold] "
                "([dim]coleta guiada + deduplicação + extração automática de sinais[/dim])"
            )
            console.print(
                "  [bold cyan]2[/bold cyan] - [bold]🖥️  2. Triagem Interativa / TUI[/bold] "
                "([dim]revisão visual com Textual: [I] Incluir ou [E] Excluir[/dim])"
            )
            console.print(
                "  [bold cyan]3[/bold cyan] - [bold]📝 3. Extração Técnica (Annex A)[/bold] "
                "([dim]codificação qualitativa de oráculos, ferramentas e evidências[/dim])"
            )
            console.print(
                "  [bold cyan]4[/bold cyan] - [bold]📊 4. Resultados do Artigo & PRISMA[/bold] "
                "([dim]PRISMA 2020 TikZ, estatísticas e análises do estudo[/dim])"
            )
            console.print("  ────────────────────────────────────────────────────")
            console.print(
                "  [bold cyan]5[/bold cyan] - [bold]🛠️  Opções Avançadas & Auditoria[/bold] "
                "([dim]fontes, planilhas Excel, métricas Kappa e Zenodo[/dim])"
            )
            console.print("  [cyan]0[/cyan] - Sair")

            choice = _safe_ask("\n[bold green]Digite o número da opção[/bold green]", default="0")

            if choice in ("0", "v", "q", "voltar", "sair", "exit", "cancelar"):
                console.print("[dim]Encerrado com sucesso. Até logo![/dim]")
                break
            if choice == "1":
                _interactive_mining_menu(DEFAULT_PROTOCOL)
            elif choice == "2":
                selected_run = _select_screening_run_id()
                if not selected_run:
                    continue
                mode = _safe_ask_choice(
                    "Modo: [1] TUI (Textual), [2] Linha a Linha (ou 0 para cancelar)",
                    valid_choices=["1", "2"],
                    default="1",
                )
                if mode is None:
                    continue
                coder = _safe_ask_coder(
                    "[bold cyan]Nome do avaliador (coder) (ou 0 para cancelar)[/bold cyan]",
                    default="revisor",
                )
                if coder is None:
                    continue
                try:
                    if mode == "2":
                        from msrkit.cli.review import screen_interactive

                        screen_interactive(
                            coder=coder,
                            run_id=selected_run,
                            protocol=DEFAULT_PROTOCOL,
                            batch=None,
                            limit=50,
                        )
                    else:
                        from msrkit.cli.review import screen_tui

                        screen_tui(
                            coder=coder,
                            run_id=selected_run,
                            protocol=DEFAULT_PROTOCOL,
                            batch=None,
                            limit=None,
                        )
                except Exception as e:
                    console.print(f"[yellow]Retorno da triagem: {e}[/yellow]")
            elif choice == "3":
                selected_run = _select_screening_run_id()
                if not selected_run:
                    continue
                coder = _safe_ask_coder(
                    "[bold cyan]Nome do avaliador (coder) (ou 0 para cancelar)[/bold cyan]",
                    default="revisor",
                )
                if coder is None:
                    continue
                try:
                    from msrkit.cli.review import coding_tui

                    coding_tui(
                        run_id=selected_run,
                        protocol=DEFAULT_PROTOCOL,
                        coder=coder,
                        status="include",
                        limit=None,
                    )
                except Exception as e:
                    console.print(f"[yellow]Retorno da extração: {e}[/yellow]")
            elif choice == "4":
                _results_menu(DEFAULT_PROTOCOL)
            elif choice == "5":
                _advanced_menu(DEFAULT_PROTOCOL)
            else:
                console.print("[red]Opção inválida! Escolha entre 0 e 5.[/red]")

            _safe_ask("\n[dim]Pressione Enter para continuar...[/dim]")
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Sessão encerrada com segurança. Até logo![/yellow]")
