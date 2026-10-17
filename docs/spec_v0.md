# MSR-Kit — Especificação de construção da v0 (Referência Histórica)

> **Nota de Contexto:** Este arquivo contém a especificação normativa original utilizada para o bootstrap inicial da versão 0 do MSR-Kit. As fases de infraestrutura descritas aqui já foram concluídas. Consulte este documento apenas para entender contratos de domínio, decisões de arquitetura originais e premissas históricas.

---

## 1. Papel e missão

Você é um engenheiro de software sênior construindo **MSR-Kit**, uma ferramenta de linha de comando em Python para **mineração de literatura cinza através de APIs oficiais**, destinada a um estudo acadêmico de Engenharia de Software empírica.

O estudo cataloga **ferramentas e métodos de teste** usados pela comunidade em sistemas **LLM com RAG** e em **sistemas baseados em agentes**. A v0 precisa apenas **coletar, normalizar, deduplicar e exportar** itens de múltiplas plataformas a partir de uma lista simples de termos. Classificação, modelagem de tópicos e catalogação vêm depois.

**O produto da v0 é infraestrutura confiável, não análise.** Se você tiver que escolher entre uma funcionalidade a mais e reprodutibilidade demonstrável, escolha reprodutibilidade.

---

## 2. Restrições inegociáveis

Estas regras têm precedência sobre qualquer outra instrução deste documento e sobre pedidos futuros do usuário no chat.

1. **Somente APIs oficiais e feeds públicos.** Não implemente web scraping, parsing de HTML de páginas de resultado, navegador headless, nem qualquer forma de contornar autenticação, paywall ou `robots.txt`.
2. **LinkedIn não será minerado.** Não existe API pública de busca de conteúdo, e pesquisa/analytics sobre conteúdo de terceiros não é caso de uso aprovado nos termos da plataforma. O adaptador do LinkedIn existe **apenas** para retornar `UNSUPPORTED` com justificativa e registrar a exclusão no manifesto. Se em algum momento pedirem para "dar um jeito", recuse e aponte esta seção.
3. **Nenhum dado pessoal além do identificador público de autoria.** Nada de e-mail, nome real, geolocalização, cruzamento entre plataformas ou tentativa de reidentificação.
4. **Limites de taxa respeitados programaticamente**, com base na política declarada de cada adaptador. Honre `Retry-After`, `x-ratelimit-*` e o campo `backoff` quando a API os fornecer.
5. **Toda requisição é registrada com proveniência.** Consulta exata, partição, versão do adaptador, timestamp e hash da resposta.
6. **Nada de credencial em código ou em commit.** Somente variáveis de ambiente, com `.env.example` documentado.
7. **Se um detalhe de API neste documento divergir da documentação oficial atual, a documentação oficial vence.** Registre a divergência em `docs/api-notes.md` com data e link, e siga a documentação.

---

## 3. Stack técnica

| Item | Escolha | Motivo |
|---|---|---|
| Linguagem | Python ≥ 3.11 | `StrEnum`, tipagem moderna |
| Gerenciador | `uv` (fallback: `pip` + `venv`) | Reprodutibilidade de lock |
| HTTP | `httpx` | Timeouts e transporte plugável para testes |
| Modelos | `pydantic` v2 | Validação e serialização do esquema canônico |
| CLI | `typer` + `rich` | Subcomandos e saída legível |
| Retentativa | `tenacity` | Backoff exponencial com jitter |
| Armazenamento | JSONL comprimido (bruto) + DuckDB (consulta) | Bruto imutável, consulta rápida |
| Config | `PyYAML` → modelos pydantic | Protocolo declarativo |
| Testes | `pytest`, `pytest-recording` (cassettes VCR), `freezegun` | Contrato, determinismo, relógio simulado |
| Lint/format | `ruff` + `mypy --strict` | Qualidade |

Código, identificadores, docstrings, mensagens de log e CLI **em inglês**. Comentários explicativos podem ser em português.

---

## 4. Estrutura do projeto

```
msrkit/
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
├── docs/
│   ├── api-notes.md          # divergências entre esta spec e a doc oficial
│   └── sources.md            # capacidades e limites por fonte, gerado por `msrkit sources --md`
├── protocols/
│   └── v0_rag_agents_testing.yaml
├── src/msrkit/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py             # YAML -> modelos pydantic
│   ├── models.py             # Item, RawItem, Query, Manifest, SourcePolicy, Availability
│   ├── governor.py           # token bucket, backoff, contabilidade de cota
│   ├── partition.py          # algoritmo genérico de particionamento
│   ├── provenance.py         # run_id, hashes, manifesto
│   ├── storage.py            # JSONL bruto + DuckDB
│   ├── dedupe.py
│   ├── keywords.py           # casamento de termos + janela de contexto
│   ├── registry.py           # descoberta e registro de adaptadores
│   └── adapters/
│       ├── base.py
│       ├── github.py
│       ├── stackexchange.py
│       ├── devto.py
│       ├── reddit.py
│       ├── hackernews.py
│       ├── huggingface.py
│       ├── rss.py            # Medium, Substack, blogs de engenharia
│       ├── bluesky.py
│       ├── x_twitter.py      # gated
│       ├── discord.py        # gated
│       └── linkedin.py       # unsupported, documentado
└── tests/
    ├── cassettes/
    ├── test_models.py
    ├── test_governor.py
    ├── test_partition.py
    ├── test_dedupe.py
    ├── test_adapters_contract.py
    └── test_determinism.py
```

---

## 5. Modelo de domínio

### 5.1 `Item` canônico

```python
class Engagement(BaseModel):
    stars: int | None = None
    forks: int | None = None
    votes: int | None = None
    reactions: int | None = None
    comments: int | None = None
    views: int | None = None

class TechContext(BaseModel):
    language: str | None = None
    path: str | None = None
    license: str | None = None
    has_ci: bool | None = None
    contributors: int | None = None
    tags: list[str] = []

class Provenance(BaseModel):
    run_id: str
    query_string: str            # a consulta exata enviada à API
    partition: str               # descrição legível da partição aplicada
    adapter: str
    adapter_version: str
    fetched_at: datetime         # UTC
    response_sha256: str
    raw_ref: str                 # caminho do arquivo bruto + offset da linha

class Item(BaseModel):
    id: str                      # sha256(f"{source}:{native_id}")[:16]
    source: str
    kind: Literal["repo","code","issue","discussion","thread","article","post","card","comment"]
    url: HttpUrl
    title: str | None
    body: str | None             # ver política de redistribuição da fonte
    body_hash: str | None        # sha256 do corpo, sempre preenchido quando há corpo
    author_handle: str | None    # somente identificador público
    created_at: datetime | None
    updated_at: datetime | None
    engagement: Engagement
    tech: TechContext
    matched_terms: list[TermHit]
    provenance: Provenance

class TermHit(BaseModel):
    term: str
    field: Literal["title","body","tags","path"]
    context: str                 # ±40 tokens ao redor da ocorrência
```

`RawItem` é a resposta bruta da API sem transformação: `{"source", "native_id", "payload": dict, "fetched_at"}`.

### 5.2 `Query` e `Availability`

```python
class Query(BaseModel):
    source: str
    terms: list[str]
    kind: str | None = None
    since: date | None = None
    until: date | None = None
    extra: dict[str, Any] = {}   # filtros específicos: tags, subreddits, linguagens
    limit: int | None = None

class Availability(BaseModel):
    status: Literal["OK","DEGRADED","UNSUPPORTED"]
    reason: str                  # sempre preenchido, mesmo em OK
    missing_env: list[str] = []
```

### 5.3 `SourcePolicy`

```python
class RateLimit(BaseModel):
    requests: int
    per_seconds: int
    burst: int = 1
    daily_cap: int | None = None

class SourcePolicy(BaseModel):
    requires_auth: bool
    auth_env_vars: list[str]
    rate_limit: RateLimit
    max_results_per_query: int | None    # teto de resultados da fonte
    max_page_size: int
    max_pages: int | None
    supports_full_text_search: bool
    supports_date_filter: bool
    redistribution: Literal["metadata_only","full_text_with_attribution","full_text"]
    tos_url: str
    docs_url: str
    notes: str
```

O campo `redistribution` é aplicado na exportação: quando for `metadata_only`, o exportador **omite o campo `body`** e mantém apenas `body_hash`, `matched_terms` e `url`.

---

## 6. Contrato do adaptador

```python
class SourceAdapter(Protocol):
    name: ClassVar[str]
    version: ClassVar[str]
    policy: ClassVar[SourcePolicy]

    def available(self) -> Availability:
        """Verifica credenciais e capacidade. NUNCA faz requisição de rede."""

    def estimate(self, q: Query) -> int | None:
        """Total de resultados, se a API informar. None se desconhecido."""

    def partition(self, q: Query) -> list[Query]:
        """Divide q até que cada partição caiba em max_results_per_query."""

    def search(self, q: Query) -> Iterator[RawItem]:
        """Itera resultados de UMA partição, paginando internamente."""

    def normalize(self, raw: RawItem) -> Item:
        """Converte payload bruto em Item canônico."""
```

`BaseAdapter` implementa: cliente httpx compartilhado, integração com o `Governor`, paginação genérica, gravação bruta e cálculo de hash. Adaptadores concretos só implementam o que é específico da fonte.

---

## 7. Especificação por fonte

> Confirme cada endpoint, parâmetro e limite contra a documentação oficial no momento da implementação. Onde divergir, siga a documentação e anote em `docs/api-notes.md`.

### 7.1 GitHub — `status: OK` — **fonte primária**

- **Auth:** `GITHUB_TOKEN` (PAT fine-grained; para dados públicos não precisa de escopo).
- **Headers:** `Accept: application/vnd.github+json`, `X-GitHub-Api-Version`, `User-Agent`.
- **Endpoints:** `GET /search/repositories`, `GET /search/code`, `GET /search/issues`.
- **Limites:** 5.000 req/h autenticado (60/h sem auth); busca 30 req/min; **busca de código exige autenticação e limita a 10 req/min**; secundários de 100 requisições concorrentes.
- **Teto crítico:** **máximo 1.000 resultados por consulta** (100 por página × 10 páginas). Este é o gargalo do projeto inteiro.
- **`estimate`:** campo `total_count` da resposta.
- **Filtro de data:** qualificadores `created:YYYY-MM-DD..YYYY-MM-DD` e `pushed:>=YYYY-MM-DD` embutidos na string `q`.
- **Eixo secundário de partição:** `language:`, depois `stars:a..b`.
- **Redistribuição:** `metadata_only` (licenças variam por repositório).

### 7.2 Stack Exchange — `status: OK`

- **Auth:** opcional, `STACKEXCHANGE_KEY` (eleva a cota diária). Sem chave a cota é baixa e o acesso anônimo é limitado à página 25.
- **Endpoint:** `GET https://api.stackexchange.com/2.3/search/advanced`
- **Parâmetros:** `q`, `tagged` (separado por `;`), `site`, `fromdate`/`todate` (**epoch em segundos**), `pagesize` (máx. 100), `page`, `filter=withbody`, `key`.
- **Sites:** `stackoverflow`, `softwareengineering`, `sqa`, `datascience`, `ai`.
- **Obrigatório:** enviar `Accept-Encoding: gzip` — a API exige gzip.
- **Honre o campo `backoff`** da resposta antes de nova requisição. Respostas são fortemente cacheadas; consultar acima de 1 req/min é considerado abusivo.
- **`estimate`:** campo `total` quando o filtro o incluir; caso contrário use `has_more`.
- **Redistribuição:** `full_text_with_attribution` — conteúdo é CC BY-SA e exige atribuição na exportação.

### 7.3 dev.to (Forem) — `status: OK`

- **Auth:** não necessária para artigos públicos.
- **Endpoints:** `GET https://dev.to/api/articles?tag=&page=&per_page=` (per_page até 1000); `GET /api/articles/{id}` para obter `body_markdown`.
- **Limitação de projeto:** a API pública **não oferece busca full-text**. Estratégia obrigatória: coletar por `tag`, depois filtrar localmente pelos termos. Marque `policy.supports_full_text_search = False` e registre no manifesto que a seleção foi local.
- **Tags iniciais:** `rag`, `llm`, `ai`, `agents`, `llmops`, `testing`, `machinelearning`, `python`.
- **Redistribuição:** `metadata_only`.

### 7.4 Reddit — `status: OK`

- **Auth:** OAuth2 client credentials. Env: `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`.
- **Token:** `POST https://www.reddit.com/api/v1/access_token` com `grant_type=client_credentials`.
- **Busca:** `GET https://oauth.reddit.com/r/{sub}/search` com `q`, `restrict_sr=1`, `sort=new`, `limit=100`, `t=all`, cursor `after`.
- **`User-Agent` customizado é obrigatório** e deve identificar a aplicação e o contato.
- **Limites:** o free tier é limitado por requisições por minuto e destina-se a uso não comercial. Configure conservadoramente e torne o valor parametrizável.
- **Subreddits iniciais:** `LocalLLaMA`, `LangChain`, `LLMDevs`, `AI_Agents`, `MachineLearning`, `softwaretesting`, `QualityAssurance`.
- **Redistribuição:** `metadata_only`.

### 7.5 Hacker News (Algolia) — `status: OK` — **incluir, alto valor**

- **Auth:** nenhuma.
- **Endpoints:** `GET https://hn.algolia.com/api/v1/search` e `.../search_by_date`
- **Parâmetros:** `query`, `tags=story,comment`, `numericFilters=created_at_i>TS,created_at_i<TS`, `hitsPerPage` (até 1000), `page`.
- **`estimate`:** campo `nbHits`.
- Busca full-text real, sem autenticação, com filtro de data nativo. É a fonte de melhor relação custo/benefício do conjunto e não estava na lista original — inclua.
- **Redistribuição:** `metadata_only`.

### 7.6 Hugging Face Hub — `status: OK`

- **Auth:** opcional, `HF_TOKEN`.
- **Endpoints:** `GET https://huggingface.co/api/models`, `/api/datasets`, `/api/spaces` com `search`, `filter`, `limit`, `full=true`.
- Alvo: datasets de avaliação, spaces de teste, cards que descrevem protocolo de avaliação.
- **Redistribuição:** `metadata_only`.

### 7.7 RSS/Atom genérico — `status: OK` — cobre **Medium** e blogs

- **Auth:** nenhuma.
- **Medium:** `https://medium.com/feed/tag/{tag}`, `https://medium.com/feed/@{user}`, `https://{publication}.medium.com/feed`
- **Limitação severa e obrigatória de documentar:** feeds RSS retornam apenas os itens mais recentes (tipicamente ~10). **Não é possível fazer recorte temporal histórico via RSS.** O Medium só entra como cobertura corrente, e o manifesto deve registrar `truncated: true` e `historical_coverage: false` para esta fonte.
- O adaptador deve aceitar uma lista arbitrária de URLs de feed no `protocol.yaml`, para incluir blogs de engenharia de empresas — que concentram muito conteúdo relevante sobre teste de RAG.
- **Redistribuição:** `metadata_only`.

### 7.8 Bluesky (AT Protocol) — `status: OK` — **substituto viável do X**

- **Auth:** `BLUESKY_HANDLE` + `BLUESKY_APP_PASSWORD`; sessão via `POST /xrpc/com.atproto.server.createSession`.
- **Busca:** `GET /xrpc/app.bsky.feed.searchPosts` com `q`, `limit`, `cursor`, `since`, `until`.
- Gratuito e com busca real. Como o X paywallou o acesso de leitura, o Bluesky é a melhor aproximação disponível para discurso curto de comunidade técnica. Documente no artigo que a substituição foi feita e por quê.
- **Redistribuição:** `metadata_only`.

### 7.9 X / Twitter — `status: DEGRADED` ou `UNSUPPORTED` — **gated**

- **Implemente o adaptador, mas não o coloque no caminho crítico da v0.**
- `available()` retorna `UNSUPPORTED` quando `X_BEARER_TOKEN` estiver ausente, com razão explicando que o acesso de leitura/busca não está disponível nas condições gratuitas.
- Com token presente, faça uma chamada de capacidade contra a documentação vigente e retorne `DEGRADED` informando a janela histórica realmente acessível no plano contratado.
- **Não fixe números de plano nem preços no código.** Eles mudam com frequência; leia a documentação oficial no momento da implementação e registre em `docs/api-notes.md` com a data da consulta.

### 7.10 Discord — `status: UNSUPPORTED` por padrão — **gated**

- Não existe busca global no Discord. A API de bot só lê mensagens de servidores onde o bot foi **instalado por um administrador**, e a leitura de conteúdo depende do intent privilegiado de conteúdo de mensagem.
- `available()` retorna `UNSUPPORTED` a menos que `DISCORD_BOT_TOKEN` e `DISCORD_GUILD_IDS` estejam configurados.
- Acrescente no `README` um aviso: minerar comunidades do Discord exige **autorização explícita dos administradores dos servidores** e deve passar por avaliação ética antes da coleta. Não implemente coleta sem esse gate.

### 7.11 LinkedIn — `status: UNSUPPORTED` — **permanente**

```python
def available(self) -> Availability:
    return Availability(
        status="UNSUPPORTED",
        reason=(
            "LinkedIn does not expose a public content-search API. "
            "Research and analytics on third-party content are not approved "
            "use cases, and scraping is prohibited by the platform terms. "
            "This adapter exists only to record the documented exclusion "
            "in the run manifest."
        ),
    )
```

`search()` levanta `SourceUnsupportedError`. Nunca implemente alternativa.

---

## 8. Governador de taxa

`Governor` mantém um *token bucket* por adaptador, alimentado por `policy.rate_limit`.

Requisitos:

1. Bloqueia antes de exceder o limite declarado — nunca reage só ao 429.
2. Lê cabeçalhos de resposta quando disponíveis (`x-ratelimit-remaining`, `x-ratelimit-reset`, `Retry-After`) e o campo `backoff` do corpo (Stack Exchange), ajustando o bucket.
3. Backoff exponencial com jitter em 429 e 403 de limite secundário; após N tentativas, marca a partição como `failed` e continua as demais — **nunca aborta a execução inteira**.
4. **Persiste o estado de cota em disco**, de modo que uma execução retomada no mesmo dia não estoure o orçamento diário.
5. Aceita um `Clock` injetável, para que os testes usem relógio simulado e não durmam de verdade.

---

## 9. Particionamento

Algoritmo genérico em `partition.py`:

```
partition(q):
  se policy.max_results_per_query for None: retorna [q]
  n = adapter.estimate(q)
  se n é None ou n <= max_results_per_query: retorna [q]
  se a janela [since, until] tem mais de 1 dia:
      divide a janela ao meio e recorre em cada metade
  senão:
      aplica o eixo secundário declarado pelo adaptador
      (ex.: language, faixa de stars, tag); se não houver eixo,
      retorna [q] marcando truncated=True e registrando a perda estimada
```

`truncated=True` **precisa** aparecer no manifesto e no relatório de `stats`. Truncamento silencioso é viés de amostragem invisível, e é o erro mais grave que esta ferramenta pode cometer.

---

## 10. Armazenamento e proveniência

```
data/
├── raw/{source}/{run_id}/{partition_hash}.jsonl.gz    # respostas verbatim
├── items/{run_id}/items.jsonl                          # normalizados
├── runs/{run_id}/manifest.json
└── msrkit.duckdb
```

O bruto é **imutável**. Renormalizar nunca deve exigir nova requisição de rede: `msrkit normalize --run <id>` reprocessa a partir do bruto.

---

## 11. Deduplicação (v0)

Duas passadas apenas:

1. **URL canônica** — minúsculas no host, remoção de parâmetros de rastreamento (`utm_*`, `ref`, `source`), remoção de barra final, resolução de encurtadores conhecidos apenas quando o destino já estiver no corpus.
2. **Hash de conteúdo** — `sha256(title + body normalizado)`.

---

## 12. Casamento de termos (v0)

`keywords.py` recebe a lista de termos do protocolo e retorna `list[TermHit]`:

- Casamento **case-insensitive**, com fronteira de palavra.
- Termos com espaço são tratados como frase exata.
- Campos varridos: `title`, `body`, `tech.tags`, `tech.path`.
- Cada ocorrência gera um `TermHit` com janela de contexto de ±40 tokens.
- Item sem nenhum `TermHit` é marcado, não descartado.

---

## 13. Protocolo declarativo

`protocols/v0_rag_agents_testing.yaml`

---

## 14. CLI

```
msrkit sources [--md]              # lista adaptadores, availability, cotas, políticas
msrkit validate <protocol.yaml>    # valida esquema + credenciais (sem rede)
msrkit plan <protocol.yaml>        # DRY RUN: partições e orçamento de requisições
msrkit run <protocol.yaml>         # executa; retomável via --resume <run_id>
msrkit normalize --run <id>        # reprocessa do bruto, sem rede
msrkit dedupe --run <id>
msrkit stats --run <id>            # itens/fonte, taxa de descarte, truncamentos
msrkit export --run <id> --format csv|jsonl|duckdb [--include-body]
```

---

## 15. Testes

Meta de cobertura: ≥ 80% em `src/msrkit/`, com `governor.py`, `partition.py` e `dedupe.py` acima de 90%. Nenhum teste pode fazer requisição de rede real.

---

## 16. Ordem de construção (Fases F1 a F7)

*(Concluídas conforme documentado em docs/PLANO.md e status_projeto_2026-10-01.md)*
