"""Interactive menu."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from rich.table import Table

from msrkit import __version__
from msrkit.cli._common import (
    DEFAULT_PROTOCOL,
    _get_latest_run_id,
    _get_registry,
    app,
    console,
)
from msrkit.cli.collect import plan, run, sources, validate
from msrkit.cli.corpus import dedupe, export, stats


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


def _manage_sources_menu(protocol_path: str) -> None:  # pragma: no cover
    """Interactive screen to toggle sources on/off based on availability."""
    from rich.prompt import Prompt

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
            title="[bold cyan]Gerenciamento de Fontes de Pesquisa[/bold cyan]",
            border_style="cyan",
            header_style="bold magenta",
        )
        table.add_column("#", style="bold cyan", width=4)
        table.add_column("Fonte", style="bold")
        table.add_column("No Protocolo", justify="center")
        table.add_column("Disponibilidade API", justify="center")
        table.add_column("Status / Requisitos")

        source_list: list[tuple[str, bool]] = []
        for idx, s_name in enumerate(all_sources, 1):
            is_enabled = config.sources[s_name].enabled if s_name in config.sources else False
            source_list.append((s_name, is_enabled))

            if s_name in registry:
                adapter = registry[s_name]()
                avail = adapter.available()
                if avail.status == "OK":
                    avail_str = "[bold green]✓ OK[/bold green]"
                    notes = "[green]Pronta (Pública/Sem chaves)[/green]"
                elif avail.status == "DEGRADED":
                    avail_str = "[bold yellow]⚠ PARCIAL[/bold yellow]"
                    notes = f"[yellow]{avail.reason}[/yellow]"
                else:
                    avail_str = "[bold red]✗ INDISPONÍVEL[/bold red]"
                    notes = f"[red]{avail.reason}[/red]"
            else:
                avail_str = "[dim]DESCONHECIDA[/dim]"
                notes = "-"

            status_str = (
                "[bold green]● ATIVADA[/bold green]" if is_enabled else "[dim]○ Desativada[/dim]"
            )
            table.add_row(str(idx), s_name, status_str, avail_str, notes)

        console.print()
        console.print(table)
        console.print("[dim]• Digite o número da fonte para alternar (Ativar ⇄ Desativar).[/dim]")
        console.print(
            "[dim]• Fontes com '✓ OK' podem ser ativadas e usadas imediatamente sem chaves.[/dim]"
        )

        choice = Prompt.ask(
            "\n[bold green]Digite o número da fonte para alternar (ou 0 para voltar)[/bold green]",
            default="0",
        )
        if choice == "0":
            break

        try:
            chosen_idx = int(choice)
            if 1 <= chosen_idx <= len(source_list):
                target_source, curr_state = source_list[chosen_idx - 1]
                new_state = not curr_state
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
    from rich.prompt import Prompt

    from msrkit.config import load_protocol

    try:
        config = load_protocol(protocol_path)
    except Exception as e:
        console.print(f"[red]Erro ao carregar protocolo: {e}[/red]")
        return

    registry = _get_registry()

    console.print("\n[bold cyan]─── 1. Escolha a Fonte para Minerar ───[/bold cyan]")
    console.print("  [bold cyan]0[/bold cyan] - [bold]Todas as fontes ativadas no protocolo[/bold]")

    sources_options: list[str] = []
    for idx, (s_name, s_cfg) in enumerate(sorted(config.sources.items()), 1):
        sources_options.append(s_name)
        status_label = "[green]● Ativada[/green]" if s_cfg.enabled else "[dim]○ Desativada[/dim]"
        avail_label = ""
        if s_name in registry:
            avail = registry[s_name]().available()
            if avail.status == "OK":
                avail_label = "[bold green][API: OK][/bold green]"
            elif avail.status == "DEGRADED":
                avail_label = "[yellow][API: Parcial/Faltam Chaves][/yellow]"
            else:
                avail_label = "[red][API: Não Suportada][/red]"

        console.print(f"  [cyan]{idx}[/cyan] - {s_name:<14} {status_label:<22} {avail_label}")

    src_choice = Prompt.ask(
        "\n[bold green]Escolha o número da fonte desejada[/bold green]",
        default="0",
    )

    selected_source: str | None = None
    if src_choice != "0":
        try:
            s_idx = int(src_choice)
            if 1 <= s_idx <= len(sources_options):
                selected_source = sources_options[s_idx - 1]
            else:
                console.print(
                    "[yellow]Opção inválida, minerando todas as fontes ativadas.[/yellow]"
                )
        except ValueError:
            console.print("[yellow]Entrada inválida, minerando todas as fontes ativadas.[/yellow]")

    console.print("\n[bold cyan]─── 2. Escolha a Quantidade de Itens para Minerar ───[/bold cyan]")
    console.print("  [cyan]1[/cyan] - ⚡ Teste Rápido (5 itens)")
    console.print("  [cyan]2[/cyan] - 🔍 Amostra Pequena (20 itens)")
    console.print("  [cyan]3[/cyan] - 📊 Amostra Média (50 itens)")
    console.print("  [cyan]4[/cyan] - 🚀 Coleta Ampla (200 itens)")
    console.print("  [cyan]5[/cyan] - ♾️  Máximo do Protocolo (sem limite rápido)")
    console.print("  [cyan]6[/cyan] - ✏️  Digitar quantidade personalizada")

    qty_choice = Prompt.ask("\n[bold green]Escolha a opção de quantidade[/bold green]", default="2")

    limit: int | None = 20
    if qty_choice == "1":
        limit = 5
    elif qty_choice == "2":
        limit = 20
    elif qty_choice == "3":
        limit = 50
    elif qty_choice == "4":
        limit = 200
    elif qty_choice == "5":
        limit = None
    elif qty_choice == "6":
        custom = Prompt.ask(
            "[bold green]Digite a quantidade exata desejada[/bold green]",
            default="20",
        )
        try:
            limit = max(1, int(custom))
        except ValueError:
            limit = 20
    else:
        limit = 20

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
    do_dedupe = Prompt.ask(
        "[bold cyan]Deseja executar a desduplicação agora e gerar também "
        "a planilha desduplicada?[/bold cyan] [s/N]",
        default="N",
    )
    if do_dedupe.strip().lower() in ("s", "sim", "y", "yes"):
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
            "[dim]Dica: Você pode desduplicar a qualquer momento através da opção 5 do menu.[/dim]"
        )


@app.command(name="menu")
def menu() -> None:  # pragma: no cover
    """Interactive terminal menu to navigate MSR-Kit easily."""
    from rich.panel import Panel
    from rich.prompt import Prompt

    while True:
        console.print()
        console.print(
            Panel.fit(
                f"[bold cyan]MSR-Kit[/bold cyan] [dim]v{__version__}[/dim]\n"
                "[italic]Mining grey literature through official APIs[/italic]",
                border_style="cyan",
            )
        )
        console.print("\n[bold]Escolha uma ação:[/bold]")
        console.print(
            "  [bold cyan]1[/bold cyan] - [bold]🎯 Iniciar Mineração[/bold] "
            "([dim]escolher fonte e quantidade flexível[/dim])"
        )
        console.print(
            "  [bold cyan]2[/bold cyan] - [bold]⚙️  Gerenciar Fontes[/bold] "
            "([dim]ativar/desativar com base na disponibilidade[/dim])"
        )
        console.print(
            "  [cyan]3[/cyan] - Status detalhado das fontes e políticas ([dim]sources[/dim])"
        )
        console.print("  [cyan]4[/cyan] - Simulação de planejamento / Dry-Run ([dim]plan[/dim])")
        console.print("  [cyan]5[/cyan] - Desduplicar última coleta ([dim]dedupe[/dim])")
        console.print("  [cyan]6[/cyan] - Exportar última coleta em CSV ([dim]export -f csv[/dim])")
        console.print("  [cyan]7[/cyan] - Estatísticas da última coleta ([dim]stats[/dim])")
        console.print("  [cyan]8[/cyan] - Validar arquivo de protocolo ([dim]validate[/dim])")
        console.print("  [cyan]0[/cyan] - Sair")

        choice = Prompt.ask("\n[bold green]Digite o número da opção[/bold green]", default="0")

        if choice == "0":
            console.print("[dim]Encerrado.[/dim]")
            break
        if choice == "1":
            _interactive_mining_menu(DEFAULT_PROTOCOL)
        elif choice == "2":
            _manage_sources_menu(DEFAULT_PROTOCOL)
        elif choice == "3":
            sources(md=False, verbose=False)
        elif choice == "4":
            plan(protocol=DEFAULT_PROTOCOL, source=None, verbose=False)
        elif choice == "5":
            which = Prompt.ask(
                "Desduplicar [1] Apenas a última coleta ou [2] Todas as coletas históricas?",
                default="1",
            )
            is_all = which == "2"
            dedupe(run_id=None, all_runs=is_all, verbose=False)

            exp_prompt = Prompt.ask(
                "\n[bold cyan]Deseja exportar a planilha CSV desduplicada agora?[/bold cyan] [S/n]",
                default="S",
            )
            if exp_prompt.strip().lower() in ("s", "sim", "y", "yes", ""):
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                suffix = "consolidado_desduplicado" if is_all else "desduplicados"
                out_file = f"data/resultados_{timestamp}_{suffix}.csv"
                export(
                    run_id=None,
                    all_runs=is_all,
                    fmt="csv",
                    include_body=False,
                    output=out_file,
                    raw=False,
                    verbose=False,
                )
                console.print(
                    f"[bold green]✓ Planilha salva em:[/bold green] "
                    f"[bold cyan]{out_file}[/bold cyan]"
                )
        elif choice == "6":
            which = Prompt.ask(
                "Exportar [1] Apenas a última coleta ou [2] Consolidado de todas as coletas?",
                default="1",
            )
            is_all = which == "2"
            kind_choice = Prompt.ask(
                "Deseja exportar [1] Versão desduplicada (se existir) ou [2] Versão bruta?",
                default="1",
            )
            is_raw = kind_choice == "2"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix_type = "brutos" if is_raw else "desduplicados"
            suffix_scope = "consolidado" if is_all else "coleta"
            out_file = f"data/resultados_{timestamp}_{suffix_scope}_{suffix_type}.csv"
            export(
                run_id=None,
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
        elif choice == "7":
            which = Prompt.ask(
                "Estatísticas de [1] Última coleta + Panorama histórico "
                "ou [2] Apenas corpus consolidado?",
                default="1",
            )
            stats(run_id=None, all_runs=(which == "2"), verbose=False)
        elif choice == "8":
            validate(protocol=DEFAULT_PROTOCOL, verbose=False)
        else:
            console.print("[red]Opção inválida![/red]")

        Prompt.ask("\n[dim]Pressione Enter para continuar...[/dim]")
