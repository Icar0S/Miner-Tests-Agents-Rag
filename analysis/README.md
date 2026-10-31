# Análises do Artigo 2

`artigo2.py` calcula, sobre um pacote de reprodução do MSR-Kit (`msrkit package`), as análises
das QP1–QP3 do guia do Artigo 2. Ele gera os números e as tabelas que o artigo inclui. Todas as
análises usam a base de **detecções automáticas**. As que dependem da codificação manual, como
H2, QP4 e QP5, ficam para quando a codificação existir.

## Como rodar

```bash
pip install -e ".[analysis]" statsmodels      # statsmodels é opcional (binomial negativa e P3)
python analysis/artigo2.py pacote_d28a.zip --out resultados/
```

Antes de existir um pacote, `--pending` grava os mesmos arquivos com marcadores visíveis, para o
artigo compilar:

```bash
python analysis/artigo2.py --pending --out resultados/
```

No Overleaf, suba a pasta `resultados/` inteira ao lado do `.tex`. Para atualizar o artigo,
basta rodar o script de novo e substituir a pasta.

## O que sai em `resultados/`

| Arquivo | Conteúdo |
|---|---|
| `macros.tex` | todos os números citados no texto, cada um com a origem em comentário (arquivo, base, parâmetros) |
| `tab_qp1.tex`, `tab_qp1_pares.tex` | pirâmide de evidência por ferramenta; pares que mais coocorrem |
| `tab_qp2.tex`, `tab_qp2_oraculos.tex` | métodos por sistema; escada de oráculos |
| `tab_qp3.tex`, `tab_qp3_metodos.tex` | engajamento mediano com IC 95% por ferramenta e por método |
| `fig_qp1_dispersao.tex` | dispersão menções (N1) × adoção externa (N3), escala log |
| `qp*.csv`, `summary.json` | tabelas completas e o resumo, com os parâmetros usados |

## Parâmetros provisórios (ADR-046)

Os valores padrão são **decisões dos autores ainda abertas** `[DECIDIR]`. Precisam ser
confirmados no ADR-046 antes da rodada confirmatória.

| Opção | Padrão | Decide |
|---|---|---|
| `--min-age-months` | 3 | idade mínima do item para entrar no engajamento |
| `--min-n` | 10 | itens mínimos para reportar a mediana de uma ferramenta ou método |
| `--listicle-k` | 4 | quantas ferramentas citadas fazem de um item uma *listicle*, excluída na sensibilidade |
| `--tau-threshold` | 0,5 | τ abaixo do qual a P1 (menção ≠ adoção) é lida como sustentada |
| `--rho-threshold` | 0,4 | \|ρ\| abaixo do qual a P2 (engajamento ≠ adoção) é lida como sustentada |
| `--min-together` | 5 | unidades mínimas em comum para um par aparecer na coocorrência |
| `--bootstrap` | 10.000 | reamostragens dos intervalos de confiança |
| `--with-repos` | não | inclui repositórios do GitHub (estrelas) no engajamento |
| `--keep-bots` | não | mantém issues automáticas (atualização de dependência, alertas) |

## Convenções (regras do guia)

- **Nível de evidência:** N1 é menção, nunca uso. N2 é uso declarado e N3 é adoção sustentada.
- **Repositório da própria ferramenta:** quando a unidade é o repositório da ferramenta (*self*), ela fica fora do ranking de adoção e aparece numa coluna à parte.
- **Frameworks:** as entradas `framework-sut` (LangChain, LangGraph…) não entram no ranking de ferramentas de teste.
- **Engajamento:** só é comparado depois de normalizado dentro de cada par fonte × tipo.
