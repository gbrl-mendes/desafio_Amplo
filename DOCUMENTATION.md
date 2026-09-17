# Documentação completa

Para instalar e rodar o exemplo básico, veja o [Início](README.md). Este documento cobre a arquitetura completa, todos os parâmetros de configuração, e todos os exemplos de uso.

## Arquitetura

| Arquivo                                                            | Função                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`tools/schema_validation.py`](tools/schema_validation.py)         | Valida o contrato de entrada (Python). Recusa a execução antes de rodar qualquer coisa se faltar coluna obrigatória, houver vazamento de gabarito, ou a chave primária for duplicada.                                                                                                                                                                                                                                                                                                                            |
| [`r/curadoria_deterministica.qmd`](r/curadoria_deterministica.qmd) | Toda a lógica determinística (R): leitura do CSV, refinamento dos três melhores hits de BLAST (busca por similaridade de sequência num banco de referência), consulta de taxonomia ao NCBI, checagem de contaminação contra os controles, faixa de tamanho esperada por marcador genético, pseudo-score (nota de confiança combinando identidade e cobertura do alinhamento), árvore filogenética entre as sequências, e checagem de ocorrência regional no GBIF (banco público de registros de biodiversidade). |
| [`r/curadoria_deterministica.R`](r/curadoria_deterministica.R)     | Versão executável, gerada do `.qmd` acima. É o arquivo que o harness realmente chama, como um subprocesso separado.                                                                                                                                                                                                                                                                                                                                                                                              |
| [`r/analise_ecologica.qmd`](r/analise_ecologica.qmd)               | Análise ecológica opcional (R): riqueza, diversidade (Shannon/Simpson), curva de acumulação, dissimilaridade entre pontos, composição taxonômica, comparação eDNA × métodos tradicionais. Roda sobre a coluna `Curated ID` do CSV já curado, não refaz identificação nenhuma.                                                                                                                                                                                                                                    |
| [`r/analise_ecologica.R`](r/analise_ecologica.R)                   | Versão executável, gerada do `.qmd` acima.                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| [`harness/orchestrator.py`](harness/orchestrator.py)               | Integração de todas as etapas: validação da entrada, execução do pipeline em R, verificação do output do R, chamada da curadoria assistida por LLM, geração do relatório narrativo, análise ecológica opcional, geração do relatório HTML único, e gravação do log em JSON de cada execução.                                                                                                                                                                                                                     |
| [`harness/llm_curation.py`](harness/llm_curation.py)               | Curadoria assistida por LLM, via Groq.                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| [`harness/report_generation.py`](harness/report_generation.py)     | Relatório narrativo da execução em markdown, também via Groq, a partir dos agregados gerados nas etapas anteriores.                                                                                                                                                                                                                                                                                                                                                                                              |
| [`harness/pdf_report.py`](harness/pdf_report.py)                   | Renderiza o markdown de `report_generation.py` como PDF (paleta e fonte da marca Amplo, iguais às do relatório HTML), a entrega final gravada em `runs/<run_id>/<run_id>_relatorio.pdf`.                                                                                                                                                                                                                                                                                                                         |
| [`harness/html_report.py`](harness/html_report.py)                 | Relatório HTML único por execução (ver "Relatório HTML" abaixo), gerado só quando a análise ecológica roda.                                                                                                                                                                                                                                                                                                                                                                                                      |
| [`harness/__main__.py`](harness/__main__.py)                       | Ponto de entrada da linha de comando (`python -m harness`).                                                                                                                                                                                                                                                                                                                                                                                                                                                      |

## Execução

```bash
# Windows
.venv\Scripts\python.exe -m harness <entrada.csv> [--config config.yaml] [--output output.csv] [--runs-dir runs/] [--llm-mode live|mock|off] [--reference spp_tradicional.csv] [--ecologia] [--groq-api-key chave]
```

```bash
# macOS, Linux, Git Bash ou WSL
.venv/bin/python -m harness <entrada.csv> [--config config.yaml] [--output output.csv] [--runs-dir runs/] [--llm-mode live|mock|off] [--reference spp_tradicional.csv] [--ecologia] [--groq-api-key chave]
```

- `<entrada.csv>`: obrigatório, a menos que `--ecologia-somente` seja usado no lugar. Schema completo em [`data/reference/asv_input_schema.yaml`](data/reference/asv_input_schema.yaml).
- `--config`: YAML opcional que sobrescreve os parâmetros padrão e permite usar um CSV com nomes de coluna diferentes dos que o sistema espera (ver "Configuração" abaixo). Fica registrado no log da execução, para rastreabilidade.
- `--output`: caminho do CSV final. Por padrão, `runs/<run_id>/<run_id>_output_pos_curadoria_LLM.csv`.
- `--runs-dir`: pasta-base onde gravar os artefatos de cada execução -- cada `run_id` ganha sua própria sub-pasta ali dentro (ver "Onde cada execução grava seus arquivos" abaixo). Por padrão, `runs/`.
- `--llm-mode`: um único parâmetro para as duas etapas que usam a Groq, a curadoria assistida (`harness/llm_curation.py`) e o relatório narrativo (`harness/report_generation.py`):
  - `live` (padrão): chama a Groq nas duas etapas, preenchendo as colunas `Assisted ID/Confidence/Justification (LLM)` e gravando o relatório em `runs/<run_id>/<run_id>_relatorio.pdf`. Sem chave configurada, as duas etapas são puladas e a execução segue normalmente.
  - `mock`: não chama a Groq, preenche as mesmas colunas e o mesmo relatório com uma resposta simulada, para testar o encadeamento sem gastar cota de API.
  - `off`: pula as duas etapas por completo. Sai só o CSV do pipeline em R e o log JSON, sem colunas assistidas e sem relatório narrativo.
- `--reference`: CSV opcional (`;`-delimitado, UTF-8, pelo menos 2 colunas: ponto amostral na primeira, taxon na última ou numa coluna chamada `Taxon_binomial`; colunas extras no meio, e o nome literal de cada coluna, são irrelevantes) com espécies já registradas por métodos tradicionais nos mesmos pontos amostrais, usado como evidência adicional na curadoria assistida (ver [Domínio e contrato](DOMINIO_E_CONTRATO.md)). Também alimenta a comparação eDNA × tradicional de `--ecologia`, se essa flag for usada. Exemplo em [`data/example/exemplo_1/spp_tradicional.csv`](data/example/exemplo_1/spp_tradicional.csv).
- `--ecologia-somente <curado.csv>`: roda só a análise ecológica sobre um CSV já curado por uma execução anterior, opcionalmente revisado à mão (ver o parágrafo sobre `Curated ID` abaixo). Mutuamente exclusivo com `<entrada.csv>`: não refaz nenhuma etapa de curadoria, só valida as colunas mínimas necessárias e chama a análise ecológica. Exemplo em "Exemplos" abaixo.
- `--groq-api-key`: informa a chave Groq. Ver "Configuração da chave da Groq" abaixo.

### Onde cada execução grava seus arquivos

Cada execução ganha sua própria sub-pasta em `runs/`, nomeada com o `run_id` (curto e ordenável cronologicamente, ex. `20260916-004104`) -- tudo que uma execução produziu fica junto, e os arquivos dentro dessa pasta levam o `run_id` no próprio nome (a pasta `ecologia/` não, já que seu nome já está dentro da pasta do `run_id`):

```
runs/<run_id>/
├── <run_id>_log.json                            # status, validação, o que a curadoria assistida revisou, avisos e erros
├── <run_id>_output_pos_curadoria_LLM.csv         # CSV final (a menos que --output aponte pra outro caminho)
├── <run_id>_relatorio.pdf                        # relatório narrativo em PDF, só com --llm-mode != off
├── <run_id>_report.html                          # relatório HTML único, só quando a análise ecológica roda
└── ecologia/                                     # tabelas (CSV) e gráficos, só com --ecologia ou --ecologia-somente
```

O CSV final sempre traz uma coluna `Curated ID`, preenchida automaticamente (`Assisted ID (LLM)` quando existir, senão a identificação determinística). É a mesma coluna que, no fluxo tradicional deste tipo de projeto, um especialista preencheria à mão antes da análise ecológica: aqui vem pré-preenchida como sugestão, e o profissional pode revisar e sobrescrever qualquer valor antes de rodar `--ecologia` (nessa mesma execução) ou `--ecologia-somente` (numa execução separada, depois de revisar o CSV). Essa versão automática não é o gabarito de curadoria humana revisado formalmente: essa validação, quando feita, acontece fora deste repositório (ver [Domínio e contrato](DOMINIO_E_CONTRATO.md)).

Código de saída do processo: `0` sucesso, `2` entrada recusada pela validação, `3` falha na execução.

### Exemplos

- **Primeiro exemplo: peixes, eDNA de água**

```bash
# Windows
.venv\Scripts\python.exe -m harness data/example/exemplo_1/eDNA_cipo_subset.csv --groq-api-key <chave>
```

```bash
# macOS, Linux, Git Bash ou WSL
.venv/bin/python -m harness data/example/exemplo_1/eDNA_cipo_subset.csv --groq-api-key <chave>
```

O dado de demonstração é um subset randomizado de 50 sequências (80 linhas, sequência × amostra) do projeto eDNA_Cipo, reduzido para manter o consumo de cota de API e o tempo de execução baixos. Sem `--output`, grava tudo em `runs/<run_id>/`: o CSV final, o log, e um relatório narrativo em PDF, tema da marca Amplo.

- **Uso com referência de amostragens tradicionais**

```bash
# Windows
.venv\Scripts\python.exe -m harness data/example/exemplo_1/eDNA_cipo_subset.csv --reference data/example/exemplo_1/spp_tradicional.csv --groq-api-key <chave>
```

```bash
# macOS, Linux, Git Bash ou WSL
.venv/bin/python -m harness data/example/exemplo_1/eDNA_cipo_subset.csv --reference data/example/exemplo_1/spp_tradicional.csv --groq-api-key <chave>
```

Mesma execução, mas a curadoria assistida também considera, por ponto de coleta, as espécies já registradas por métodos tradicionais na tabela de `--reference`.

- **Segundo exemplo: plantas, metabarcoding de raízes**

```bash
# Windows
.venv\Scripts\python.exe -m harness data/example/exemplo_2/roots_metabar_subset.csv --reference data/example/exemplo_2/roots_metabar_spp_tradicional.csv --config data/example/exemplo_2/config.yaml --groq-api-key <chave>
```

```bash
# macOS, Linux, Git Bash ou WSL
.venv/bin/python -m harness data/example/exemplo_2/roots_metabar_subset.csv --reference data/example/exemplo_2/roots_metabar_spp_tradicional.csv --config data/example/exemplo_2/config.yaml --groq-api-key <chave>
```

Segundo dado de demonstração, do projeto `roots_metabar` (raízes, primer ITS2, plantas), de domínio taxonômico e origem diferentes do primeiro. Não tem dados de latitude/longitude, então a checagem regional por GBIF é pulada. O `--config` aponta o grupo taxonômico alvo para `Plantae` e mapeia o nome de coluna de controle próprio deste dataset (`PCR control`) para o nome interno esperado.

- **Com análise ecológica na mesma execução**

```bash
# Windows
.venv\Scripts\python.exe -m harness data/example/exemplo_1/eDNA_cipo_subset.csv --reference data/example/exemplo_1/spp_tradicional.csv --ecologia --groq-api-key <chave>
```

```bash
# macOS, Linux, Git Bash ou WSL
.venv/bin/python -m harness data/example/exemplo_1/eDNA_cipo_subset.csv --reference data/example/exemplo_1/spp_tradicional.csv --ecologia --groq-api-key <chave>
```

Mesma execução do primeiro exemplo, mas com `--ecologia`: além do CSV curado, grava riqueza/diversidade por ponto, curva de acumulação, dissimilaridade entre pontos, composição taxonômica e a comparação eDNA × tradicional em `runs/<run_id>/ecologia/`, mais o relatório HTML único, que abre sozinho no navegador ao final (ver "Relatório HTML" abaixo).

- **Análise ecológica separada, sobre um CSV revisado à mão**

```bash
# Windows
.venv\Scripts\python.exe -m harness --ecologia-somente runs\<run_id_anterior>\<run_id_anterior>_output_pos_curadoria_LLM.csv --reference data/example/exemplo_1/spp_tradicional.csv --groq-api-key <chave>
```

```bash
# macOS, Linux, Git Bash ou WSL
.venv/bin/python -m harness --ecologia-somente runs/<run_id_anterior>/<run_id_anterior>_output_pos_curadoria_LLM.csv --reference data/example/exemplo_1/spp_tradicional.csv --groq-api-key <chave>
```

Depois de revisar `Curated ID` manualmente num CSV já curado por uma execução anterior, roda só a análise ecológica sobre essa versão revisada, sem refazer a curadoria. Gera seu próprio log e seu próprio relatório HTML, mais enxuto (sem tabela de entrada nem resumos do determinístico, já que essa execução não rodou essas etapas, ver "Relatório HTML").

## Relatório HTML

Toda execução que roda a análise ecológica (`--ecologia` ou `--ecologia-somente`) gera também um relatório único em `runs/<run_id>/<run_id>_report.html`, autocontido (sem depender de internet ou de outros arquivos para abrir), com navegação lateral por seção (estilo MultiQC) e tabelas com barra de rolagem própria.

Assim que fica pronto, o relatório abre sozinho no navegador, numa janela nova. A barra lateral também traz um botão "Abrir pasta desta execução", que abre a pasta `runs/<run_id>/` (CSV final, PDF, e a pasta `ecologia/`).

O relatório sempre descreve só o que aquela execução específica fez, nunca finge ter visto uma etapa que não rodou nela:

- **Execução completa** (`--ecologia`): identificação do projeto e pesquisador, resumo geral dos metadados, tabela de entrada bruta, resumo de cada etapa da curadoria determinística, tabela de saída do determinístico, resultado da curadoria assistida por LLM, tabela final, e os gráficos interativos da análise ecológica.
- **`--ecologia-somente`**: um aviso destacado no topo, deixando claro que essa análise partiu de um CSV já curado por uma execução anterior, possivelmente revisado à mão; a tabela usada; e os gráficos interativos.

## Configuração da chave da Groq

A curadoria assistida e o relatório narrativo usam a Groq. A chave é resolvida nesta ordem de prioridade: `--groq-api-key` na chamada, variável de ambiente `GROQ_API_KEY`, ou um `.env` local (nunca commitado; `.env.example` documenta o formato). A chave será fornecida por e-mail, já que não pode ser disponibilizada num repositório público, sob risco de cancelamento.

## Configuração: `--config`

O parâmetro `--config` aceita um arquivo YAML opcional, lido pela validação em Python (`tools/schema_validation.py`) e pelo pipeline em R (`r/curadoria_deterministica.R`), com os defaults em `DEFAULT_CONFIG`. Declare só os parâmetros que precisam mudar para o seu projeto; o resto fica no valor padrão.

Exemplo com todos os parâmetros preenchidos:

```yaml
colunas_alias:
  Pesquisador: Researcher
  Projeto: Project
  Ponto_coleta: Ponto
  Lat: Latitude
  Long: Longitude

contaminacao:
  fold_change_threshold: 10

amplicon_por_primer:
  MiFish2: [140, 200]
  COI: [300, 320]

identificacao:
  pseudoscore_thresholds:
    especie: 98
    genero: 95
    familia: 90
    ordem: 80
    classe: 60

arvore_filogenetica:
  k_vizinhos: 5

taxons_alvo:
  grupos: ["Actinopteri"]

checagem_regional:
  fonte: gbif
  buffer_graus: 0.1

ecologia:
  coluna_grupo: Ponto
  coluna_id: Curated ID
  rank_taxonomico: Family (NCBI)
  metodo_dissimilaridade: bray
  min_amostras_curva_acumulacao: 2
  metadados_grupo: [Habitat, Rios]
```

**`colunas_alias`.** Resolve a diferença entre o nome de coluna que o seu CSV usa e o nome que o sistema espera internamente. Esse segundo nome, o "canônico", é a lista fixa definida em [`data/reference/asv_input_schema.yaml`](data/reference/asv_input_schema.yaml), o mesmo contrato que `tools/schema_validation.py` usa pra checar coluna obrigatória.

Cada par declarado é `nome no seu CSV: nome canônico`. Por exemplo, se a coluna de pesquisador no seu arquivo se chama `Pesquisador` em vez de `Researcher`:

```yaml
colunas_alias:
  Pesquisador: Researcher
```

Isso renomeia a coluna para `Researcher` antes de qualquer outra etapa rodar, inclusive antes da checagem de coluna obrigatória.

- Só é preciso declarar as colunas cujo nome já não bater com o canônico; o resto é assumido como já estando no nome certo.
- Cobre qualquer coluna do contrato: `Researcher`, `Project`, `Primer`, `Sample`, `Unique_File_name`, `ASV absolute abundance`, os campos de cada um dos 3 hits de BLAST (`1_subject header`, `1_staxid`, `1_subject`, `1_indentity`, `1_qcovhsp`, e o mesmo para `2_`/`3_`).
- `Ponto` é a única coluna semântica obrigatória. `Latitude`/`Longitude` são opcionais; sem elas, as etapas que dependem de cada uma são puladas com aviso. `Latitude`/`Longitude` esperam grau decimal codificado como inteiro (ex. `-19420314` para `-19.420314`) e são convertidas automaticamente.

**`contaminacao.fold_change_threshold`.** Limiar do Fold Change (abundância relativa na amostra dividida pela abundância relativa máxima da mesma sequência no controle referenciado) abaixo do qual uma detecção vira `"Possible contamination"`. Default `10`.

**`amplicon_por_primer`.** Faixa de tamanho esperada (pb) por primer, usada para marcar `Primer expected length` e como piso mínimo na árvore filogenética. Primer não declarado aqui tem a faixa estimada automaticamente a partir da distribuição de tamanho das próprias sequências daquele primer no dataset (quartis, com margem de 1,5× o intervalo interquartil), avisando quando faz essa estimativa; com poucos dados demais para estimar (menos de 4 sequências), a coluna fica `NA` em vez de arriscar um valor. Default só declara `MiFish2: [140, 200]`.

**`identificacao.pseudoscore_thresholds`.** Limiares do pseudo-score que definem até que nível taxonômico uma sequência é identificada: acima do limiar de `especie`, aceita a identificação do BLAST; senão sobe para `genero`, `familia`, `ordem`, `classe`, nessa ordem; abaixo de todos, vira `"Unidentified"`. Default: `especie: 98, genero: 95, familia: 90, ordem: 80, classe: 60`.

**`arvore_filogenetica.k_vizinhos`.** Quantos vizinhos filogenéticos mais próximos entram na coluna `Vizinhos filogenéticos (k)`, evidência usada pela curadoria assistida. Default `5`.

**`taxons_alvo.grupos`.** Grupos ecológicos considerados dentro do escopo para `Possible target taxon`. Grupos disponíveis: `Actinopteri` (peixes ósseos), `Metazoa` (reino animal inteiro, mais amplo), `Plantae`, `Benthos`, `Zooplankton`, `Periphyton`, `Phytoplankton`; default `["Actinopteri"]`.

**`checagem_regional`.**

- `fonte`: só `"gbif"` está implementado; outro valor pula a etapa com aviso.
- `buffer_graus`: margem (grau decimal) somada em cada lado do bounding box calculado a partir do min/max de `Latitude`/`Longitude` dos dados. Default `0.1` (~11 km).
- `area_bbox` (opcional): bounding box fixo, usado no lugar do cálculo automático quando declarado.

**`ecologia`.** Usado com `--ecologia` ou `--ecologia-somente`.

- `coluna_grupo`: unidade espacial (site × taxon) em toda a análise. Default `Ponto`.
- `coluna_id`: identidade de cada ASV usada nas contagens de riqueza/diversidade. Default `Curated ID`.
- `rank_taxonomico`: coluna de rank taxonômico usada no gráfico de composição. Default `Family (NCBI)`.
- `metodo_dissimilaridade`: `bray` ou `jaccard`. Default `bray`.
- `min_amostras_curva_acumulacao`: mínimo de amostras para tentar uma curva de acumulação. Default `2`.
- `metadados_grupo`: colunas de metadado opcional pra quebrar riqueza/diversidade por categoria. Default `[Habitat, Rios]`.

## Formatos de entrada e saída

Entrada e saída são ambos CSV delimitado por `;`, decimal `,`, UTF-8, com aspas no padrão CSV comum. A lista completa de colunas de saída está em [Domínio e contrato](DOMINIO_E_CONTRATO.md), e o contrato de entrada em [`data/reference/asv_input_schema.yaml`](data/reference/asv_input_schema.yaml).

## Rodando os testes em detalhe

Camada R, cobre hoje a consulta ao GBIF (retry em limite de taxa):

```bash
Rscript r/tests/run_tests.R
```

Comandos completos (Python e R) em [Início](README.md#rodando-os-testes).
