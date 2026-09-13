# Relatório de exemplo — execução real sobre o dado de demonstração

Este não é um exemplo hipotético: é a saída de uma execução real do sistema, do início ao
fim, sobre o dado de demonstração do repositório. Nenhum número abaixo foi inventado — todos
vêm do CSV final e do log JSON gerados por essa execução.

## Contexto da execução

```bash
python -m harness data/example/dasafio_Amplo-ASVs_BLASTr_output-2026-09-12.csv \
  --output data/example/saida_exemplo_curada.csv --llm-mode=live
```

O CSV completo desta execução está commitado em
[`data/example/saida_exemplo_curada.csv`](data/example/saida_exemplo_curada.csv) — todos os
números e trechos citados abaixo vêm dele.

| | |
|---|---|
| Entrada | `dasafio_Amplo-ASVs_BLASTr_output-2026-09-12.csv` — 815 linhas (ASV × amostra), 21 amostras reais + 2 controles, projeto eDNA_Cipo, primer MiFish2 |
| Config | padrão (nenhum `--config` informado) |
| Início | 2026-09-13 03:25:16 UTC |
| Fim | 2026-09-13 03:53:46 UTC (≈ 28 min — a maior parte em consultas ao NCBI/GBIF e à Groq, respeitando limites de taxa de cada serviço) |
| Status | `success` |
| Validação de entrada | OK, sem colunas obrigatórias ausentes, sem vazamento de gabarito, sem chave primária duplicada |

## O que foi encontrado (resumo quantitativo)

**Identificação (determinística):**

| Nível alcançado | Linhas |
|---|---|
| Species | 544 |
| Genus | 68 |
| Order | 21 |
| Family | 10 |
| Unidentified (sem hit confiável) | 172 |

**Contaminação:** 771 "True detection", 44 "Possible contamination" (Fold Change abaixo do
limiar de 10x contra pelo menos um controle referenciado).

**Faixa de amplicon (MiFish2, 140–200pb):** 628 "in range", 187 "out of range" — os "out of
range" são majoritariamente ASVs muito curtas (30–90pb, prováveis primer-dimer/artefato de
sequenciamento) ou muito longas, corretamente separadas da faixa esperada do primer.

**Curadoria assistida por LLM:** das 443 ASVs únicas na tabela, 228 precisaram de revisão
(identificação não-espécie, hit não confiável, ou espécie sem nenhum registro regional no
GBIF) — as outras 215 já estavam suficientemente confiáveis e não passaram pelo LLM. Dessas
228, **39 receberam uma identificação diferente** da determinística (17%) — não porque o
LLM "decidiu diferente" arbitrariamente, mas porque a evidência adicional (plausibilidade
geográfica/biológica, consenso dos vizinhos filogenéticos) mudou a leitura do mesmo conjunto
de fatos.

## Onde a curadoria assistida agregou valor de forma concreta

### Contaminação que o Fold Change não conseguia pegar

Duas ASVs foram identificadas com 100% de identidade a **`Sus scrofa`** (porco) e
**`Homo sapiens`** (humano) — contaminação óbvia num estudo de peixes. O determinístico
marcou as duas como `"True detection"`, não porque confiasse na identificação, mas porque
**nenhuma delas nunca apareceu em nenhum dos controles de laboratório** (`Control presence:
False`) — sem controle pra comparar, não há Fold Change pra calcular, e a regra documentada
é não penalizar por ausência de evidência. Essa é uma lacuna conhecida e documentada do
método de Fold Change puro (ver [DOMINIO_E_CONTRATO.md](DOMINIO_E_CONTRATO.md)).

A curadoria assistida fechou exatamente essa lacuna, usando plausibilidade biológica que o
cálculo de Fold Change não tem como capturar:

> **`Sus scrofa`** → `Mammalia`, confiança Baixa: *"O melhor hit do BLAST foi Sus scrofa com
> pseudo-score 100, mas a espécie é um mamífero (classe Mammalia) e não ocorre na bacia do
> rio São Francisco, nem há registros de peixes correspondentes [...]"*

> **`Homo sapiens`** → `Unidentified`, confiança Baixa: *"Embora o BLAST retorne Homo sapiens
> com pseudo-score 100, essa espécie não ocorre na bacia do rio São Francisco [...] a
> presença de DNA humano em amostras de água é tipicamente contaminação, portanto a
> identificação não pode ser [confirmada]."*

### Cautela geográfica em identificações de espécie com BLAST forte

> **`Gymnotus sylvius`** (pseudo-score 100, nível espécie) → rebaixado para `Gymnotus sp.`,
> confiança Média: *"[...] não há registros da espécie no GBIF para a bacia do São Francisco,
> indicando possível ausência ou erro de referência; a presença em 9 amostras reais sem
> sinais de contaminação [...]"*

O determinístico não tem como fazer esse julgamento sozinho — ele só teria a opção de
aceitar cegamente o pseudo-score alto ou de não ter critério nenhum pra questionar. A skill
pondera as duas evidências (BLAST forte × ausência regional) em vez de escolher uma ignorando
a outra.

### Recuperando precisão quando o filtro de pseudo-score foi conservador demais

> **`Loricariidae`** (família, pseudo-score baixo o bastante pra não chegar a gênero) →
> `Parotocinclus`, confiança Média: *"O BLAST retornou Parotocinclus maculicauda como melhor
> hit [...] a taxonomia NCBI confirma o gênero Parotocinclus. Contudo, não há registros da
> espécie no GBIF [...] e a ASV foi encontrada em apenas uma amostra [...]"*

Aqui a skill foi na direção oposta: usou a mesma evidência (hit do BLAST + taxonomia NCBI)
que o determinístico já tinha, mas não descartou o gênero só porque o pseudo-score
não alcançava o limiar — e ainda assim manteve a confiança em "Média", não "Alta". Isso é o
comportamento que o desenho pretendia: nem aceitar cegamente o determinístico, nem inventar
uma certeza que a evidência não sustenta.

## Um limite real encontrado nesta própria execução

Nesta execução, **34 das 228 consultas ao LLM falharam por limite de uso do plano gratuito da
Groq** (rate limit de 200.000 tokens/dia para o modelo usado, atingido depois de revisar a
maior parte das 228 ASVs). O sistema não travou nem produziu um resultado incompleto
silenciosamente: cada falha foi registrada individualmente (ASV por ASV, no log do run), as
outras 194 revisões continuaram normalmente, e as linhas correspondentes ficaram com as
colunas assistidas vazias mais uma nota de erro em vez de um valor inventado.

Isso é uma limitação real e esperada de rodar a curadoria assistida sobre uma cota gratuita
com um dataset deste tamanho — documentada aqui em vez de escondida. Rodar em `--llm-mode=off`
(ou aguardar a renovação diária da cota) resolve para quem quiser o resultado 100% revisado.

## Rastreabilidade

Cada execução grava um log JSON completo em `runs/<timestamp>.json` — validação, exit code
do R, quantas ASVs foram revisadas, cada erro individual da curadoria assistida — o
suficiente para reconstruir "o que foi feito e por quê" sem precisar rodar nada de novo.
