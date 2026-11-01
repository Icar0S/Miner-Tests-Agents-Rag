#!/usr/bin/env python3
"""Coleta completa local: as etapas do workflow Coleta, sem o teto de tempo do Actions.

O job do Actions tem 240 min. Uma coleta no tamanho da d28a (100 itens por consulta e
todos os repositórios do GitHub enriquecidos) passa disso, então a coleta oficial roda
na máquina local ou no container. Este script executa as mesmas etapas de
.github/workflows/collect.yml, grava tudo num log em data/logs/ e, se cair no meio,
continua de onde parou com --retomar.

Uso, na raiz do repositório:

    python scripts/coleta_local.py                     # coleta completa (padrões abaixo)
    python scripts/coleta_local.py --retomar <run_id>  # continua uma execução interrompida
    python scripts/coleta_local.py --help

Credenciais vêm do .env: GITHUB_TOKEN é obrigatório para o GitHub; STACKEXCHANGE_KEY e
HF_TOKEN aumentam as cotas. Janela e termos vêm só do protocolo: MSRKIT_WINDOW_* e
MSRKIT_TERMS* do .env são ignoradas aqui, como no Actions, para que a coleta oficial
siga o protocolo versionado.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTOCOL = "protocols/v0_rag_agents_testing.yaml"
# Os mesmos grupos de fontes do workflow Coleta.
PUBLIC = "hackernews,devto,stackexchange,huggingface,rss"
MANUAL = "github"
PAID = "x_twitter"
UNAVAILABLE = "linkedin,discord,bluesky"
# Parâmetros de busca que só o protocolo define na coleta oficial.
SEARCH_OVERRIDES = ("MSRKIT_WINDOW_", "MSRKIT_TERMS")


@dataclass
class Step:
    name: str
    args: list[str]  # argumentos do `msrkit`
    fatal: bool = True  # se falhar, para a coleta (as demais seguem e são relatadas)


Runner = Callable[[Step, Mapping[str, str], Path, TextIO], int]


def child_env(
    environ: Mapping[str, str], dotenv: Mapping[str, str], public: str, manual: str
) -> dict[str, str]:
    """Ambiente dos comandos: grupos de fontes fixos e busca só pelo protocolo.

    Uma variável vazia não sobrepõe o protocolo e impede o msrkit de ler a do .env
    (variáveis já definidas têm precedência sobre o arquivo).
    """
    env = dict(environ)
    for key in {*environ, *dotenv}:
        if key.startswith(SEARCH_OVERRIDES):
            env[key] = ""
    env.update(
        MSRKIT_SOURCES_PUBLIC=public,
        MSRKIT_SOURCES_MANUAL=manual,
        MSRKIT_SOURCES_PAID=PAID,
        MSRKIT_SOURCES_UNAVAILABLE=UNAVAILABLE,
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
    )
    return env


def pipeline(
    run_id: str,
    protocol: str,
    *,
    enrich: bool,
    enrich_limit: int = 0,
    graphql: bool = True,
    analyses: bool = True,
) -> list[Step]:
    """Etapas depois da coleta, na ordem do workflow, todas presas a `run_id`."""
    p, r = ["-p", protocol], ["--run", run_id]
    steps = [Step("Deduplicação", ["dedupe", *r])]
    if enrich:
        cmd = ["enrich", *p, *r]
        if enrich_limit:
            cmd += ["--limit", str(enrich_limit)]
        if graphql:
            cmd.append("--graphql")
        steps.append(Step("Enriquecimento do GitHub (N2/N3)", cmd))
    steps += [
        Step("Extração de ferramentas e métodos", ["extract", *p, *r]),
        Step("Planilha de triagem", ["screen", "export", *p, *r]),
        Step("Fluxo PRISMA", ["prisma", *r]),
        Step("Relatório da mineração", ["report", *r]),
        Step(
            "Exportação CSV", ["export", *r, "-f", "csv", "-o", f"data/exports/itens_{run_id}.csv"]
        ),
        Step("Pacote de reprodutibilidade", ["package", *p, *r]),
    ]
    if analyses:
        detections = ["--basis", "detections"]
        steps += [
            Step(
                "A1: frequência",
                ["analyze", "frequency", *detections, "--top", "30", *p, *r],
                False,
            ),
            Step(
                "A2: coocorrência",
                [
                    "analyze",
                    "frequency",
                    *detections,
                    "--pairs",
                    "tool-tool",
                    "--top",
                    "15",
                    *p,
                    *r,
                ],
                False,
            ),
            Step("A4: escada de oráculos", ["analyze", "oracles", *detections, *p, *r], False),
            Step("A6: RAG × agentes", ["analyze", "compare", *detections, *p, *r], False),
            Step(
                "A5: descoberta aberta",
                [
                    "analyze",
                    "topics",
                    "--status",
                    "all",
                    "--topics",
                    "12",
                    "--clusters",
                    "12",
                    *p,
                    *r,
                ],
                False,
            ),
            Step("Amostra de precisão", ["precision", "sample", "--n", "200", *r], False),
        ]
    steps.append(Step("Estatísticas", ["stats", *r], False))
    return steps


def run_dirs(runs_dir: Path) -> set[str]:
    if not runs_dir.is_dir():
        return set()
    return {p.name for p in runs_dir.iterdir() if p.is_dir() and p.name != "consolidated"}


def new_run_id(before: set[str], runs_dir: Path) -> str | None:
    """O run criado pela coleta: o diretório novo em data/runs/."""
    created = sorted(run_dirs(runs_dir) - before)
    return created[-1] if created else None


def run_step(step: Step, env: Mapping[str, str], cwd: Path, log: TextIO) -> int:
    """Roda `msrkit <args>` com a saída na tela e no log."""
    cmd = [sys.executable, "-m", "msrkit.cli", *step.args]
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        log.write(line)
    return proc.wait()


def keep_awake(on: bool) -> None:
    """No Windows, impede a suspensão por inatividade enquanto a coleta roda."""
    if sys.platform != "win32":
        return
    import ctypes

    es_continuous, es_system_required = 0x80000000, 0x00000001
    flags = es_continuous | (es_system_required if on else 0)
    # windll só existe no Windows: mypy fora dele não conhece o atributo.
    ctypes.windll.kernel32.SetThreadExecutionState(flags)  # type: ignore[attr-defined,unused-ignore]


def utf8_output() -> None:
    """Saída em UTF-8: no Windows, redirecionada para arquivo, o padrão é cp1252."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _duration(seconds: float) -> str:
    minutes, secs = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min {secs:02d} s"


def _say(log: TextIO, text: str = "") -> None:
    print(text, flush=True)
    log.write(text + "\n")
    log.flush()


def _resume_hint(log: TextIO, run_id: str | None) -> None:
    if run_id:
        _say(log, "\nPara continuar de onde parou:")
        _say(log, f"    python scripts/coleta_local.py --retomar {run_id}")
    else:
        _say(log, "\nA coleta não chegou a criar uma execução; rode o script de novo.")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Coleta completa local (as etapas do workflow Coleta, sem teto de tempo).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--por-consulta", type=int, default=100,
                    help="máximo de itens por consulta (0 = sem teto)")  # fmt: skip
    ap.add_argument("--limite", type=int, default=0,
                    help="máximo de itens por fonte (0 = o do protocolo)")  # fmt: skip
    ap.add_argument("--enriquecer", type=int, default=0,
                    help="máximo de repositórios do GitHub enriquecidos (0 = todos)")  # fmt: skip
    ap.add_argument("--sem-graphql", action="store_true",
                    help="enriquecer só pela API REST (mais lento)")  # fmt: skip
    ap.add_argument("--fontes", default=PUBLIC, help="fontes públicas, separadas por vírgula")
    ap.add_argument("--fontes-manuais", default=MANUAL,
                    help="fontes com credencial; vazio (\"\") deixa o GitHub de fora")  # fmt: skip
    ap.add_argument("--retomar", metavar="RUN_ID", help="continua uma execução interrompida")
    ap.add_argument("--sem-analises", action="store_true",
                    help="pula as análises preliminares A1–A6 e a amostra de precisão")  # fmt: skip
    ap.add_argument("--protocolo", default=DEFAULT_PROTOCOL, help="arquivo do protocolo")
    ap.add_argument("--pasta", type=Path, default=ROOT,
                    help="pasta de trabalho; os dados vão para <pasta>/data")  # fmt: skip
    return ap.parse_args(argv)


def main(argv: list[str] | None = None, runner: Runner = run_step) -> int:
    utf8_output()
    args = parse_args(argv)
    try:
        from msrkit.envfile import parse_dotenv
    except ImportError:
        print(
            "msrkit não está instalado neste Python. Na raiz do repositório:\n"
            "    python -m venv .venv\n"
            '    .venv\\Scripts\\pip install -e ".[analysis]"   (Linux/macOS: .venv/bin/pip)\n'
            "e rode o script com o Python do .venv."
        )
        return 2

    work = Path(args.pasta).resolve()
    protocol = Path(args.protocolo)
    protocol = protocol if protocol.is_absolute() else ROOT / protocol
    if not protocol.is_file():
        print(f"Protocolo não encontrado: {protocol}")
        return 2
    # O mesmo .env que o msrkit carrega: o da pasta de trabalho, senão o da raiz.
    env_file = work / ".env" if (work / ".env").is_file() else ROOT / ".env"
    dotenv = parse_dotenv(env_file.read_text(encoding="utf-8-sig")) if env_file.is_file() else {}

    def credential(name: str) -> bool:
        return bool(os.environ.get(name) or dotenv.get(name))

    manual = [s.strip() for s in args.fontes_manuais.split(",") if s.strip()]
    github = "github" in manual
    if github and not credential("GITHUB_TOKEN"):
        print(
            "GITHUB_TOKEN não encontrado (nem no ambiente nem em .env).\n"
            "Sem token o GitHub responde a 60 req/h, sem busca de código, e a coleta levaria\n"
            "muitas horas. Crie o .env a partir do .env.example e preencha GITHUB_TOKEN\n"
            '(https://github.com/settings/tokens), ou rode sem o GitHub com --fontes-manuais "".'
        )
        return 2

    env = child_env(os.environ, dotenv, args.fontes, ",".join(manual))
    data = work / "data"
    (data / "logs").mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    log_path = data / "logs" / f"coleta_{stamp}.log"
    started = time.monotonic()
    timings: list[tuple[str, float, int]] = []
    run_id: str | None = args.retomar

    with open(log_path, "w", encoding="utf-8") as log:

        def step(s: Step) -> int:
            _say(log, f"\n=== {s.name} · msrkit {' '.join(s.args)}")
            t0 = time.monotonic()
            rc = runner(s, env, work, log)
            timings.append((s.name, time.monotonic() - t0, rc))
            if rc:
                _say(log, f"--- {s.name} terminou com erro (código {rc})")
            return rc

        _say(log, "Coleta completa local — MSR-Kit")
        _say(log, f"  protocolo:      {protocol}")
        _say(log, f"  dados:          {data}")
        _say(log, f"  log:            {log_path}")
        _say(log, f"  fontes:         {args.fontes}" + (f",{','.join(manual)}" if manual else ""))
        per_query = args.por_consulta or "sem teto"
        enrich_scope = args.enriquecer or "todos"
        _say(log, f"  por consulta:   {per_query} · enriquecer: {enrich_scope}"
                  + ("" if args.sem_graphql else " (GraphQL)"))  # fmt: skip
        for name in ("STACKEXCHANGE_KEY", "HF_TOKEN"):
            if not credential(name):
                _say(log, f"  aviso: {name} vazio; a fonte roda com cota menor.")
        ignored = sorted(k for k in env if k.startswith(SEARCH_OVERRIDES) and dotenv.get(k))
        if ignored:
            _say(log, f"  ignorando do .env (vale o protocolo): {', '.join(ignored)}")
        if args.retomar:
            _say(log, f"  retomando:      {args.retomar}")
        _say(
            log, "  A d28a levou ~2 h só na coleta; o enriquecimento pode levar mais algumas horas."
        )

        keep_awake(True)
        try:
            if step(Step("Validação do protocolo e credenciais", ["validate", str(protocol)])):
                return 1
            collect = ["run", str(protocol)]
            if args.retomar:
                collect += ["--resume", args.retomar]
            else:
                if args.por_consulta:
                    collect += ["--per-query-limit", str(args.por_consulta)]
                if args.limite:
                    collect += ["--limit", str(args.limite)]
            before = run_dirs(data / "runs")
            rc = step(Step("Coleta nas fontes", collect))
            run_id = run_id or new_run_id(before, data / "runs")
            if rc or not run_id:
                _resume_hint(log, run_id)
                return rc or 1

            for s in pipeline(
                run_id,
                str(protocol),
                enrich=github,
                enrich_limit=args.enriquecer,
                graphql=not args.sem_graphql,
                analyses=not args.sem_analises,
            ):
                if step(s) and s.fatal:
                    _resume_hint(log, run_id)
                    return 1
        except KeyboardInterrupt:
            _say(log, "\nInterrompido.")
            _resume_hint(log, run_id)
            return 130
        finally:
            keep_awake(False)

        _say(log, f"\n=== Concluído em {_duration(time.monotonic() - started)} · execução {run_id}")
        for name, seconds, rc in timings:
            _say(log, f"  {'ok  ' if rc == 0 else 'ERRO'}  {_duration(seconds):>14}  {name}")
        report = data / "reports" / run_id / "relatorio_mineracao.json"
        if report.is_file():
            rep = json.loads(report.read_text(encoding="utf-8"))
            _say(log, f"\n  {rep['identified']} identificados → {rep['unique']} itens únicos")
            for warning in rep.get("warnings", []):
                _say(log, f"  aviso: {warning}")
        _say(log, "\nArquivos:")
        _say(log, f"  relatório:  {data / 'reports' / run_id / 'relatorio_mineracao.md'}")
        _say(log, f"  PRISMA:     {data / 'reports' / run_id / 'prisma.md'}")
        _say(log, f"  CSV:        {data / 'exports' / f'itens_{run_id}.csv'}")
        _say(log, f"  pacote:     {data / 'packages' / f'msrkit_{run_id}.zip'}")
        _say(log, f"  log:        {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
