# Sources Documentation

Viabilidade de cada plataforma, verificada em **setembro/2026** contra a documentação
oficial e o código do MSR-Kit. Espelha a §6.1 do Protocolo E2 v2.

Run `msrkit sources` for current availability status on your environment, and
`msrkit sources --md` for the adapter policy table.

## A. Viáveis — API oficial gratuita e adaptador implementado

| Fonte | Acesso e custo | Limites que afetam a coleta | Adaptador |
|---|---|---|---|
| github | Gratuita com token pessoal (PAT) | 5.000 req/h; busca 30 req/min; busca de código 10 req/min, só autenticada; **teto de 1.000 resultados por consulta** | repositórios, código, issues |
| stackexchange | Gratuita; chave opcional | 300 req/dia sem chave, 10.000 com chave; `backoff` obrigatório | perguntas com corpo |
| huggingface | Gratuita; token opcional | ~1.000 req por janela de 5 min (usuário gratuito); anônimo, menos | models, datasets, spaces |
| devto | Gratuita, sem autenticação | Sem busca textual: coleta por tag e filtro local | artigos |
| rss | Gratuita (Medium não tem API de leitura) | Só os ~10 itens mais recentes por feed; sem histórico | feeds genéricos |
| hackernews | Gratuita, sem autenticação | ~10.000 req/h por IP; **teto de 1.000 resultados por consulta** | stories e comentários |

## B. Condicionadas — aprovação, credencial, custo ou implementação pendente

| Fonte | Situação | Adaptador |
|---|---|---|
| reddit | Desde 11/2025 (Responsible Builder Policy), credenciais OAuth novas exigem **aprovação manual prévia**, sem prazo nem garantia; Pushshift restrito a moderadores | implementado; depende de credencial aprovada |
| bluesky | Gratuita; `searchPosts` exige conta e senha de aplicativo | implementado |
| GH Archive | Dumps gratuitos; BigQuery com 1 TiB/mês grátis e US$ 6,25 por TiB excedente | não implementado |
| YouTube | Data API gratuita (10.000 unidades/dia; busca = 100 unidades); legendas de vídeos de terceiros inacessíveis | não implementado |
| Busca web paga (ex.: Brave) | US$ 5 de crédito/mês (~1.000 buscas), depois US$ 5 por 1.000; ranking não reprodutível | não implementado |

## C. Inviáveis

| Fonte | Motivo | Adaptador |
|---|---|---|
| x_twitter | Leitura só paga: pague-por-uso, a partir de US$ 0,005 por post lido; planos Basic/Pro fechados a novos desenvolvedores | stub (não coleta) |
| linkedin | Sem API de busca de conteúdo; termos vedam coleta de conteúdo de terceiros | stub permanente |
| discord | Sem busca global; bot instalado por administrador, intenção privilegiada e revisão ética | stub (não coleta) |
| Google Custom Search | Fechada a novos clientes; descontinuação em 01/01/2027 | não implementado |
| Bing Web Search | Aposentada em 11/08/2025 | não implementado |

## Documentação consultada

- GitHub: https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
- Stack Exchange: https://api.stackexchange.com/docs/throttle · https://api.stackexchange.com/docs/advanced-search
- Hugging Face: https://huggingface.co/docs/hub/rate-limits
- dev.to: https://developers.forem.com/api/v1
- Medium: https://help.medium.com/hc/en-us/articles/214874118
- Hacker News: https://hn.algolia.com/api · https://github.com/algolia/hn-search/issues/230
- Reddit: https://support.reddithelp.com/hc/en-us/articles/42728983564564
- Bluesky: https://docs.bsky.app/docs/api/app-bsky-feed-search-posts
- GH Archive / BigQuery: https://gharchive.org · https://cloud.google.com/bigquery/pricing
- YouTube: https://developers.google.com/youtube/v3/docs/captions/download
- X: https://docs.x.com/x-api/getting-started/pricing
- Discord: https://support-dev.discord.com/hc/en-us/articles/4404772028055
- Google Custom Search: https://developers.google.com/custom-search/v1/overview
- Bing: https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement
