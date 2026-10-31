# Comparativo das minerações: fork (`a2a8`) × repositório original (`d28a`)

Análise de 30/10/2026 sobre os dois zips:

- `msrkit-dataset-36528947297.zip`: última mineração deste fork, run `2026-09-29T06-01-39Z-a2a8`.
- `msrkit_replication_2026-10-20T18-01-36Z-d28a.zip`: última mineração do repositório original (upstream), run `2026-10-20T18-01-36Z-d28a`.

Todos os números foram recalculados direto de `corpus/items.jsonl`, `evidence/detections.jsonl`, `evidence/github_repos.jsonl` e `run/manifest.json` de cada pacote. Nenhum foi copiado dos relatórios.

---

## Resposta curta

**A mineração `d28a` (repositório original) é a mais rica pelo Protocolo E2 v2 e deve ser a base dos dois artigos.** Comparada à `a2a8`, ela:

- tem 3,9× mais itens únicos (11.433 contra 2.916), com mais volume em **todas** as fontes, não só no GitHub;
- enriqueceu 100% dos repositórios do corpus (contra 22% no fork), o que multiplica a evidência N2 por 14 e a N3 por 8;
- é a única com recall medido no gold set e com amostra de precisão avaliada.

A `a2a8` leva vantagem em três pontos de transparência: o PRISMA registra o que não foi recuperado, as issues são particionadas por data e as análises A1–A6 vêm no pacote. Ela serve como execução-piloto e como registro histórico.

---

## Por que os datasets são tão diferentes

**Não são dois mineradores diferentes. É o mesmo minerador, em versões e configurações diferentes.**

### 1. O repositório original já contém todo o fork

O `upstream/main` incorporou a Fase 2 feita no fork (commits A, B, T, V, N e M) e as execuções R1–R5, e depois avançou mais **44 commits** (82 arquivos alterados).

- O fork não tem nenhum commit que o original não tenha.
- O `main` do fork está 56 commits atrás do original. As execuções R1–R5 ficaram só no branch `claude/dreamy-gauss-jgnapi`.
- A `a2a8` rodou no commit `0de5639`; a `d28a`, no `0f1d4d3`.

### 2. A escala da execução mudou muito (é a maior causa da diferença)

| Parâmetro | `a2a8` (fork) | `d28a` (original) |
|---|---|---|
| Data e ambiente | 29/09, GitHub Actions, 57 min de coleta | 20/10, Docker local, 1 h 50 min de coleta |
| Itens por consulta (`per_query_limit`) | 10 | 100 |
| Teto de itens por fonte | 5.000 | 10.000 |
| Repositórios enriquecidos | 300 (teto fixo) | 5.500 (sem teto) |
| Termos de busca | 30 (20 EN + 10 PT) | 36 (20 EN + 16 PT)\* |

\* Os 6 termos PT novos são apenas as versões sem acento de termos que já existiam (`avaliacao de RAG`, `regressao de prompt` etc.). O vocabulário não foi ampliado.

### 3. Correções de bugs que mudaram os dados

- As duas consultas literais de repositório (`repos-rag` e `repos-agentes`) **retornavam 0 resultados** no fork por erro de sintaxe. No original, retornam 100 cada.
- Três repositórios do gazetteer mudaram de dono (ragas, giskard e NeMo-Guardrails), e as consultas de issues dessas ferramentas falhavam.
- Também entraram: orçamento justo entre consultas, teto de páginas no dev.to e filtro de bots.

---

## Quadro comparativo pelos critérios do protocolo

| Critério (seção do E2 v2) | `a2a8` (fork) | `d28a` (original) | Melhor |
|---|---|---|---|
| Itens únicos após deduplicação | 2.916 | 11.433 | d28a |
| GitHub / Hacker News / Stack Exchange / Hugging Face / dev.to | 2.459 / 202 / 117 / 122 / 10 | 9.252 / 999 / 520 / 562 / 99 | d28a |
| RSS (sem cobertura histórica) | 6 | 1 | irrelevante |
| Consultas do GitHub cortadas pelo limite por consulta (C2) | 260 de 324 (80%), cortadas em 10 itens | 78 de 178 (44%), cortadas em 100 itens | d28a |
| Repositórios do corpus com enriquecimento (§3.4, N3) | 300 de 1.378 (22%) | 5.449 de 5.449 (100%) | d28a |
| Detecções N1 / N2 / N3 | 1.441 / 307 / 142 | 4.460 / 4.306 / 1.105 | d28a |
| Itens com ao menos uma detecção | 1.204 (41%) | 5.678 (50%) | d28a |
| Entradas do gazetteer detectadas | 50 | 51 (só `pytrec-eval` é nova) | empate |
| Itens com rótulo de sistema RAG/agente (§3.1) | 1.201 | 3.782 | d28a |
| Estrato em português (I4) | 17 itens | 90 itens | d28a |
| Itens com algum engajamento > 0 | 1.984 | 5.435 | d28a |
| Consultas literais de repositório (§7.3) | 0 resultados (bug) | 100 + 100 | d28a |
| Recall no gold set (§10.3) | não medido | 38/53 (71,7%) | d28a |
| Precisão do extrator (§12.3) | amostra sorteada, sem julgamento | 98%, mas por pré-avaliação automática | d28a (parcial) |
| Títulos de bots e dependências (aproximação por título) | 2,1% | 0,6% | d28a |
| PRISMA registra o que não foi recuperado | sim (≈292 mil estimados) | não (registra 0) | a2a8 |
| Issues particionadas por janelas de data (C1) | sim (187 consultas) | não (36 consultas na janela inteira) | a2a8 |
| Análises A1–A6 dentro do pacote | todas, em CSV | só relatório e figuras (A1) | a2a8 |
| Itens fora da janela temporal | 4 | 29 | a2a8 |
| Detecções N3 com ≥2 contribuidores e ≥2 meses de commits | 100% | 100% | empate |
| Triagem, codificação e κ (§8, §11, §15) | não feito | não feito | empate (pendente) |

O que é idêntico nos dois: o esquema dos arquivos, as 6 fontes ativas, a janela (01/2023 a 08/2026), o método de deduplicação (URL, conteúdo e quase-duplicata com limiar 0,85) e a regra de N3.

---

## O que um tem e o outro não

- Só 1.315 URLs são comuns. **1.601 URLs da `a2a8` (55%) não estão na `d28a`**, quase todas issues do GitHub (1.398).
  - No fork, as issues foram buscadas por termo em janelas de data (187 consultas × 10 itens). No original, por termo na janela inteira (36 consultas × 100 itens).
  - Os resultados da busca do GitHub também mudam com o tempo.
- A união dos dois daria 13.034 URLs. Mas juntar execuções de versões diferentes quebra a proveniência. É melhor tratar a baixa sobreposição como um achado sobre a instabilidade das buscas, que entra na avaliação do Artigo 1.
- A variedade de ferramentas é praticamente a mesma (50 contra 51 entradas). **O ganho da `d28a` está na profundidade da evidência:** ferramentas que no fork tinham de 0 a 8 repositórios N3 passam a ter de 20 a 50.

---

## O que corrigir ou declarar antes de usar a `d28a` num artigo

1. **O PRISMA subnotifica o que ficou de fora.** O `prisma.md` informa "0 não recuperados" e "0 consultas truncadas pelo teto da fonte". Porém o GitHub bateu o teto de 10.000 itens e 4 consultas de issues em português nem rodaram, como o próprio `relatorio_final.md` admite. As estimativas de total foram desligadas quando `per_query_limit ≤ 100`, então o tamanho da população no GitHub é desconhecido.
2. **As consultas de repositório mudaram além da sintaxe.** O `comparativo_evolucao_runs.md` descreve a correção como `(RAG OR "retrieval augmented")` e `(agent OR agentic)`. O protocolo empacotado, porém, tem `RAG (...)` e `agentic (...)`, ou seja, perdeu "retrieval augmented" e "agent". Há duas saídas: corrigir para o que foi documentado e rodar de novo, ou registrar como desvio de protocolo.
3. **A precisão de 98% não foi medida por humanos.** O relatório a chama de "pré-avaliação assistida automatizada", e as notas de 196 das 200 linhas se resumem a duas frases-modelo. Dois juízes humanos precisam avaliar a amostra antes da publicação.
4. **O gold set foi montado depois das execuções de setembro** (commits de 15 a 20/10), quando os autores já tinham visto os corpora anteriores. Isso deve ser declarado. O recall no Stack Exchange foi 0/3.
5. **29 cards do Hugging Face estão fora da janela** (2022-07, 2026-09 e 2026-10). Filtre ou explique.
6. **Nada foi triado nem codificado, em nenhum dos dois.** Todas as contagens vêm das detecções automáticas, não da codificação por consenso.
7. **41% do corpus são hits de código**, sem texto, sem engajamento e sem rótulo RAG/agente; 67% dos itens não têm rótulo de sistema. Esses hits servem para N2/N3, mas não para as perguntas sobre técnicas, oráculos e engajamento.

---

## Recomendação

1. **Use a `d28a` como base dos dois artigos.** Resolva os itens 1 e 2 com uma execução final congelada (o ideal) ou com uma execução complementar declarada.
2. **Mantenha `a2a8`, `412d`, `6dec` e `98f6` como histórico.** No Artigo 1, essas execuções mostram a evolução do instrumento e a sensibilidade aos parâmetros. Só não as trate como experimento controlado, porque versão do código e parâmetros mudaram ao mesmo tempo.
3. **Sincronize o fork com o original.** Como o fork não tem commits próprios, o `main` pode avançar direto para `upstream/main` (fast-forward), sem conflito. Manter os dois em paralelo duplica esforço.

---

## Ressalvas sobre o `comparativo_evolucao_runs.md`

- A descrição da correção das consultas de repositório não bate com o protocolo empacotado (item 2 acima).
- A saturação da `a2a8` aparece como "lote 116/117". No `saturation_detections.csv` da própria `a2a8`, a última entrada nova surge no lote 68 de 117, e 90% das entradas já estão presentes no lote 11.
- De todo modo, essa saturação é medida sobre um gazetteer fechado de 51 entradas: satura por construção e não demonstra saturação temática. A saturação que o §11 pede deve ser medida sobre os códigos da codificação manual.
- Os "36 termos" são apresentados como expansão do vocabulário, mas são acentuação duplicada.
