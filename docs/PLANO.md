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
| C4b | **Conteúdo integral — dev.to:** corpo completo do artigo via API oficial (`/api/articles/{id}`) | 🔄 |
| C4c | **Conteúdo integral — links (HN, RSS):** comando `msrkit fetch`, opcional, que baixa o texto do artigo apontado respeitando robots.txt; texto guardado só localmente, nunca exportado | ⏳ |

## Registro

Cada etapa é entregue num commit próprio, cujo título começa com o identificador da etapa
(ex.: `C1: ...`) e que atualiza esta tabela.
