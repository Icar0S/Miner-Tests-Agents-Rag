# Plano de trabalho — Protocolo E2 v2

Acompanhamento da implementação das **condições de execução** do MSR-Kit definidas na
§6.5 do Protocolo E2 v2 (setembro/2026). A coleta (semanas 8–10 do cronograma) só começa
quando todas as condições estiverem concluídas.

Legenda: ✅ concluído · 🔄 em andamento · ⏳ pendente

| Etapa | Descrição | Status |
|---|---|---|
| E0 | Documentação alinhada ao protocolo v2 (fontes, viabilidade, ADRs, README) | ✅ |
| C1 | **Particionamento acionado** — GitHub e Hacker News particionam por data quando a estimativa passa de 1.000 resultados | ✅ |
| C2 | **Truncamento registrado** — manifesto registra `truncated=true` em consultas cortadas e em fontes sem cobertura histórica (RSS) | ✅ |
| C3 | **Filtro local sem perda** — casamento de termos tolera flexões e hífens; descartes de dev.to/RSS são registrados no manifesto | ✅ |
| C6 | **Filtro de tags do Stack Exchange** — `tagged` com semântica OU, como documentado pela API | ✅ |
| C5 | **Léxico em português** — termos em pt no protocolo, com estrato identificável | ✅ |
| C4a | **Conteúdo integral — Stack Exchange:** respostas das threads coletadas junto com a pergunta | ✅ |
| C4b | **Conteúdo integral — dev.to:** corpo completo do artigo via API oficial (`/api/articles/{id}`) | ✅ |
| C4c | **Conteúdo integral — links (HN, RSS):** comando `msrkit fetch`, opcional, que baixa o texto do artigo apontado respeitando robots.txt; texto guardado só localmente, nunca exportado | ✅ |

## Fase 2 — Ajustes e melhorias do minerador

Fase 1 (acima, concluída) deixou a **coleta** confiável. A Fase 2 cobre o que o Protocolo
E2 v2 ainda exige do MSR-Kit para responder às RQs: consultas por fonte, rotulagem
RAG/agente, evidência N2/N3, triagem, extração, análise e validação do próprio instrumento.

**Prioridades**, casadas com o cronograma do protocolo (§18.1) e com o risco "minerador
virar o produto" (§19):

- **P0 — antes do piloto (semana 5):** o piloto de 30 itens precisa delas.
- **P1 — antes da coleta (até a semana 7):** entram na v0.2; depois disso o escopo do
  minerador congela e nenhum requisito novo é aceito até o catálogo fechar.
- **P2 — depois da coleta:** análise e empacotamento; podem ser scripts em `analysis/`,
  fora do núcleo, sem reabrir o minerador.

### Bloco A — Coleta alinhada ao protocolo

| Etapa | Descrição | Protocolo | Prioridade | Status |
|---|---|---|---|---|
| A0 | **Governador de taxa ligado ao `run`** (bug encontrado na implementação): o `run` criava os adaptadores sem o token bucket nem as cotas diárias; cota do Stack Exchange travada em 300 mesmo com chave; cota esgotada não parava a fonte | RNF2 | P0 | ✅ |
| A1 | **Consultas por fonte** no `protocol.yaml` (`queries:`), além dos termos globais: busca de código (`"from ragas import"`, `filename:promptfooconfig.yaml`), workflows (`path:.github/workflows <âncora>`) e issues das ferramentas-âncora (`repo:<owner>/<repo>`) | §7.3 | P0 | ✅ |
| A2 | **Grupos de conceito e rótulo de sistema**: `concepts: {rag, agente, teste}` no protocolo; cada item recebe `sistema: rag \| agente \| rag+agente` pelos termos que casaram; consultas combinam sistema E teste | §3.1, RQ5 | P0 | ✅ |
| A3 | **Gazetteer versionado** (`gazetteer.yaml`): id canônico, aliases, família, padrões de import/dependência/config/CLI e regras de desambiguação; validado pelo `msrkit validate` | §7.2, §12.2 | P0 | ✅ |
| A4 | **`plan` realista**: opção `--estimate` que consulta as estimativas, mostra partições, truncamentos previstos e custo em requisições por fonte (hoje é heurística fixa) | §6.4, §7.4 | P1 | ✅ |
| A5 | **Retomada por partição**: checkpoint granular para que `--resume` continue da partição interrompida, não da fonte inteira | RNF3 | P1 | ✅ |

### Bloco B — Evidência artefatual (N2/N3), núcleo de RQ1 e RQ3

| Etapa | Descrição | Protocolo | Prioridade | Status |
|---|---|---|---|---|
| B1 | **Enriquecimento de repositórios GitHub**: árvore de arquivos (diretórios de teste, configs de avaliação), workflows de CI, nº de contribuidores e meses distintos com commits; preenche `has_ci` e `contributors`, hoje nunca preenchidos | §3.4 (N3) | P0 | ✅ |
| B2 | **Extrator de ferramentas e métodos** (`msrkit extract`): gazetteer + regex + sinais estruturais (import, dependência em pyproject/requirements/package.json, arquivo de config, invocação em CI), com nível N1/N2/N3 e trecho de evidência por detecção | RF6, §12.1 | P0 | ✅ |
| B3 | **Desambiguação**: nomes que colidem com palavras comuns só contam com co-ocorrência do léxico ou sinal estrutural; aliases convergem para o id canônico | §12.2 | P1 | ✅ |

### Bloco T — Triagem e extração com humano no laço

| Etapa | Descrição | Protocolo | Prioridade | Status |
|---|---|---|---|---|
| T1 | **Quase-duplicatas** (MinHash/LSH) como 3ª passada da deduplicação, com limiar configurável e calibrado no piloto | RF5, §11 | P1 | ✅ |
| T2 | **Planilha de triagem** exportada (critérios I1–I5/E1–E6, decisão, justificativa, codificador) e **importada de volta** para o corpus | §8, §11 | P0 | ✅ |
| T3 | **Ordenação da triagem** por relevância (termos casados, sinais N2/N3), em lotes fixos | §11 | P1 | ✅ |
| T4 | **Formulário de extração** (Anexo A) e colunas de **qualidade da literatura cinza** (§9), exportados e importados do mesmo modo | Anexo A, §9 | P1 | ✅ |
| T5 | **Concordância entre codificadores** (`msrkit agreement`): κ de Cohen por dimensão sobre a amostra de dupla codificação | §15 | P1 | ✅ |
| T6 | **Fluxo PRISMA** gerado das contagens de manifesto, descartes, deduplicação e decisões de triagem | RF8 | P1 | ✅ |

### Bloco V — Validação do instrumento (Design Science)

| Etapa | Descrição | Protocolo | Prioridade | Status |
|---|---|---|---|---|
| V1 | **Recall sobre gold set**: formato do gold set (~50 artefatos) e comando que mede quantos o minerador recuperou | §10.3 | P0 | ✅ |
| V2 | **Amostra de precisão**: exporta 200 detecções estratificadas por fonte e nível para revisão manual e calcula a precisão | §12.3 | P1 | 🔄 |
| V3 | **Testes de contrato com cassettes**: gravar respostas reais por adaptador (vcrpy já é dependência; `tests/cassettes/` está vazio) e checar o esquema das APIs | §10.2 | P1 | ⏳ |
| V4 | **Pacote de reprodutibilidade** (`msrkit package`): protocolo, manifestos, hashes, versão e corpus de metadados prontos para o Zenodo | §10.3, D2–D3 | P2 | ⏳ |

### Bloco N — Análises (A1–A6 do protocolo)

| Etapa | Descrição | Protocolo | Prioridade | Status |
|---|---|---|---|---|
| N1 | Frequência por ferramenta e método, por nível de evidência, e co-ocorrência ferramenta × método | A1, A2 | P2 | ⏳ |
| N2 | Matriz de cobertura modo de falha × ferramenta/método, para RAG e agentes | A3 | P2 | ⏳ |
| N3 | Distribuição na escada de oráculos e por agregação (teste de H2) | A4 | P2 | ⏳ |
| N4 | LDA + clusterização para o passe de descoberta aberta, com a fração do catálogo vinda dele | A5, §7.6 | P2 | ⏳ |
| N5 | Comparação RAG × agentes e curva de saturação por lote | A6, §11 | P2 | ⏳ |

### Bloco M — Manutenção e qualidade de código

| Etapa | Descrição | Prioridade | Status |
|---|---|---|---|
| M1 | **Dividir `cli.py`** (1.647 linhas, 10 comandos) em módulos por comando, sem mudar comportamento | P1 | ⏳ |
| M2 | **mypy no CI** (configurado como strict, mas hoje com 42 erros e fora do CI) e `ruff format --check` | P1 | ⏳ |
| M3 | **Documentação** (`docs/manual.md`, README) acompanhando cada etapa | contínua | ⏳ |

### Marcos

1. **Piloto (P0):** A1–A3, B1–B2, T2 e V1 prontos. No piloto: conferir a semântica OU de
   `tagged` no Stack Exchange (ADR-017), decidir a inclusão do Hacker News e calibrar o
   limiar de quase-duplicata.
2. **v0.2 congelada (P1):** restante de A, B, T e V1–V3, mais M1–M2. Reverificar
   `docs/sources.md` antes da coleta.
3. **Pós-coleta (P2):** bloco N e V4.

## Registro

Cada etapa é entregue num commit próprio, cujo título começa com o identificador da etapa
(ex.: `C1: ...`, `A1: ...`) e que atualiza estas tabelas.
