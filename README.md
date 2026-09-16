# Desafio Amplo

Este projeto implementa minha resposta ao desafio técnico do processo seletivo para a vaga de Cientista de Dados Pleno na Amplo Engenharia, desenvolvido segundo os parâmetros pré-definidos para os concorrentes.

O problema resolvido, descrito em detalhes em [DOMINIO_E_CONTRATO.md](DOMINIO_E_CONTRATO.md), é hipotético: uma consultoria ambiental recebe uma tabela bruta de sequências de DNA ambiental e precisa transformá-la numa base de dados confiável sobre quais espécies existem numa área de estudo. Este é o sistema que a cientista de dados dessa consultoria executaria para resolver essa demanda, em qualquer projeto de metabarcoding que receber, independente do grupo taxonômico ou do substrato amostrado. Ele identifica cada sequência taxonomicamente, separa detecção real de contaminação, confere se o tamanho é compatível com o marcador genético usado, e, para os casos que essas três etapas não resolvem sozinhas, consulta um modelo de linguagem (LLM) com a evidência já calculada para uma segunda opinião. O resultado dessa segunda opinião nunca sobrescreve o resultado determinístico, entra como colunas adicionais. No final, o profissional humano ainda tem a possibilidade de definir, com base em tudo que foi gerado, quais são as identificações mais parcimoniosas.

Veja [DOMINIO_E_CONTRATO.md](DOMINIO_E_CONTRATO.md) para o problema, o domínio, os formatos e as limitações declaradas deste sistema, e [RELATORIO_EXEMPLO.md](RELATORIO_EXEMPLO.md) para uma execução real sobre o dado de demonstração, com achados concretos. As decisões de arquitetura e a calibração de cada parâmetro estão documentadas nos comentários do próprio [`r/curadoria_deterministica.qmd`](r/curadoria_deterministica.qmd), seção a seção.

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
| [`harness/pdf_report.py`](harness/pdf_report.py)                   | Renderiza o markdown de `report_generation.py` como PDF (tema Cayman), a entrega final gravada em `runs/<run_id>_relatorio.pdf`.                                                                                                                                                                                                                                                                                                                                                                                 |
| [`harness/html_report.py`](harness/html_report.py)                 | Relatório HTML único por execução (ver "Relatório HTML" abaixo), gerado só quando a análise ecológica roda.                                                                                                                                                                                                                                                                                                                                                                                                      |
| [`harness/__main__.py`](harness/__main__.py)                       | Ponto de entrada da linha de comando (`python -m harness`).                                                                                                                                                                                                                                                                                                                                                                                                                                                      |

## Requisitos

- **Python 3.11+**, de preferência num ambiente virtual (`pip install -r requirements.txt`). **No Windows, não use o Python da Microsoft Store** para criar esse ambiente virtual -- ele roda com uma virtualização de sistema de arquivos que pode fazer pacotes R instalados depois do venv criado ficarem invisíveis para os subprocessos R deste harness, mesmo com `Rscript r/install_packages.R` rodando sem erro (o harness detecta esse caso e avisa na tela, mas evitar já de início é mais simples). Instale o Python direto de [python.org](https://www.python.org/downloads/) em vez disso; para conferir se um Python já instalado é da Store, rode `python -c "import sys; print(sys.executable)"` e veja se o caminho contém `WindowsApps`.
- **R 4.x** com os pacotes listados em [`r/install_packages.R`](r/install_packages.R). Rode uma vez: `Rscript r/install_packages.R` (inclui tidyverse, yaml, taxize, ape, rgbif, vegan, jsonlite, plotly, htmlwidgets, ggdendro e, via Bioconductor, DECIPHER/Biostrings; instala também um Pandoc portátil se nenhum Pandoc de sistema for encontrado, necessário pros gráficos interativos autocontidos). **No Windows, o instalador do R nem sempre adiciona `Rscript` ao PATH** -- se o comando acima der "'Rscript' is not recognized", chame pelo caminho completo (ex. `& "C:\Program Files\R\R-4.6.1\bin\Rscript.exe" r\install_packages.R`) ou adicione essa pasta `bin` ao PATH do seu usuário. Isso afeta só esse comando manual: o harness (`python -m harness`) já procura o R sozinho, no PATH e nos locais comuns de instalação, sem precisar dessa configuração.
- Acesso à internet durante a execução: as consultas ao NCBI (taxonomia) e ao GBIF (ocorrência regional) fazem parte do pipeline determinístico, não só da parte de LLM.
- Opcional: uma chave de API gratuita da [Groq](https://console.groq.com/keys) para a curadoria assistida e o relatório narrativo (ver "Configuração da chave da Groq" abaixo). Sem chave, as duas etapas são puladas automaticamente e o resto do pipeline roda normalmente.

## Instalação

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac
pip install -r .\requirements.txt
Rscript .\r\install_packages.R
```

No Windows, se `.venv\Scripts\activate` recusar rodar com "running scripts is disabled on this system", a política de execução do PowerShell está bloqueando o script. Rode antes, na mesma sessão do terminal:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
```

Vale só para essa sessão, não precisa de administrador.

## Execução

```bash
python -m harness <entrada.csv> [--config config.yaml] [--output output_pos_curadoria_LLM-AAAA-MM-DD.csv] [--runs-dir runs/] [--llm-mode live|mock|off] [--reference spp_tradicional.csv] [--ecologia] [--groq-api-key chave]
```

- `<entrada.csv>`: obrigatório, a menos que `--ecologia-somente` seja usado no lugar. Schema completo em [`data/reference/asv_input_schema.yaml`](data/reference/asv_input_schema.yaml).
- `--config`: YAML opcional que sobrescreve os parâmetros padrão e permite usar um CSV com nomes de coluna diferentes dos que o sistema espera (ver "Configuração" abaixo). Fica registrado no log da execução, para rastreabilidade.
- `--output`: caminho do CSV final. Por padrão, `runs/output_pos_curadoria_LLM-<AAAA-MM-DD>.csv` (data da execução).
- `--runs-dir`: pasta onde gravar o log de cada execução. Por padrão, `runs/`.
- `--llm-mode`: um único parâmetro para as duas etapas que usam a Groq, a curadoria assistida (`harness/llm_curation.py`) e o relatório narrativo (`harness/report_generation.py`):
  - `live` (padrão): chama a Groq nas duas etapas, preenchendo as colunas `Assisted ID/Confidence/Justification (LLM)` e gravando o relatório em `runs/<run_id>_relatorio.pdf`. Sem chave configurada, as duas etapas são puladas e a execução segue normalmente.
  - `mock`: não chama a Groq, preenche as mesmas colunas e o mesmo relatório com uma resposta simulada, para testar o encadeamento sem gastar cota de API.
  - `off`: pula as duas etapas por completo. Sai só o CSV do pipeline em R e o log JSON, sem colunas assistidas e sem relatório narrativo.
- `--reference`: CSV opcional (`;`-delimitado, UTF-8, pelo menos 2 colunas: ponto amostral na primeira, taxon na última ou numa coluna chamada `Taxon_binomial`; colunas extras no meio, e o nome literal de cada coluna, são irrelevantes) com espécies já registradas por métodos tradicionais nos mesmos pontos amostrais, usado como evidência adicional na curadoria assistida (ver [DOMINIO_E_CONTRATO.md](DOMINIO_E_CONTRATO.md)). Também alimenta a comparação eDNA × tradicional de `--ecologia`, se essa flag for usada. Exemplo em [`data/example/exemplo_1/spp_tradicional.csv`](data/example/exemplo_1/spp_tradicional.csv).
- `--ecologia`: roda a análise ecológica ([`r/analise_ecologica.qmd`](r/analise_ecologica.qmd)) logo após a curadoria assistida, sobre a coluna `Curated ID` do CSV final. Escreve tabelas e gráficos em `runs/<run_id>_ecologia/`, e também gera o relatório HTML único (ver "Relatório HTML" abaixo). Desligada por padrão; uma falha aqui nunca invalida a curadoria já concluída, só fica registrada no log.
- `--ecologia-somente <curado.csv>`: roda só a análise ecológica sobre um CSV já curado por uma execução anterior, opcionalmente revisado à mão (ver o parágrafo sobre `Curated ID` abaixo). Mutuamente exclusivo com `<entrada.csv>`: não refaz nenhuma etapa de curadoria, só valida as colunas mínimas necessárias e chama a análise ecológica. Exemplo em "Exemplos" abaixo.
- `--groq-api-key`: informam a chave Groq. Ver "Configuração da chave da Groq" abaixo para mais informações.

O CSV final sempre traz uma coluna `Curated ID`, preenchida automaticamente (`Assisted ID (LLM)` quando existir, senão a identificação determinística). É a mesma coluna que, no fluxo tradicional deste tipo de projeto, um especialista preencheria à mão antes da análise ecológica: aqui vem pré-preenchida como sugestão, e o profissional pode revisar e sobrescrever qualquer valor antes de rodar `--ecologia` (nessa mesma execução) ou `--ecologia-somente` (numa execução separada, depois de revisar o CSV). Essa versão automática não é o gabarito de curadoria humana revisado formalmente: essa validação, quando feita, acontece fora deste repositório (ver [DOMINIO_E_CONTRATO.md](DOMINIO_E_CONTRATO.md)).

Código de saída do processo: `0` sucesso, `2` entrada recusada pela validação, `3` falha na execução.

### Exemplos

- **Primeiro exemplo: peixes, eDNA de água**

```bash
python -m harness data/example/exemplo_1/dasafio_Amplo-eDNA_cipo_subset_output-2026-09-13.csv --groq-api-key <chave>
```

O dado de demonstração é um subset randomizado de 50 sequências (80 linhas, sequência × amostra) do projeto eDNA_Cipo, reduzido para manter o consumo de cota de API e o tempo de execução baixos. Sem `--output`, grava `runs/output_pos_curadoria_LLM-<AAAA-MM-DD>.csv`, um log em `runs/<run_id>.json` e um relatório narrativo em PDF (tema Cayman) em `runs/<run_id>_relatorio.pdf`.

- **Uso com referência de amostragens tradicionais**

```bash
python -m harness data/example/exemplo_1/dasafio_Amplo-eDNA_cipo_subset_output-2026-09-13.csv --reference data/example/exemplo_1/spp_tradicional.csv --groq-api-key <chave>
```

Mesma execução, mas a curadoria assistida também considera, por ponto de coleta, as espécies já registradas por métodos tradicionais na tabela de `--reference`.

- **Segundo exemplo: plantas, metabarcoding de raízes**

```bash
python -m harness data/example/exemplo_2/dasafio_Amplo-roots_metabar_subset_output-2026-09-15.csv --reference data/example/exemplo_2/roots_metabar_spp_tradicional.csv --config data/example/exemplo_2/config.yaml --groq-api-key <chave>
```

Segundo dado de demonstração, do projeto `roots_metabar` (raízes, primer ITS2, plantas), de domínio taxonômico e origem diferentes do primeiro. Não tem dados de latitude/longitude, então a checagem regional por GBIF é pulada. O `--config` aponta o grupo taxonômico alvo para `Plantae` e mapeia o nome de coluna de controle próprio deste dataset (`PCR control`) para o nome interno esperado.

- **Com análise ecológica na mesma execução**

```bash
python -m harness data/example/exemplo_1/dasafio_Amplo-eDNA_cipo_subset_output-2026-09-13.csv --reference data/example/exemplo_1/spp_tradicional.csv --ecologia --groq-api-key <chave>
```

Mesma execução do primeiro exemplo, mas com `--ecologia`: além do CSV curado, grava riqueza/diversidade por ponto, curva de acumulação, dissimilaridade entre pontos, composição taxonômica e a comparação eDNA × tradicional em `runs/<run_id>_ecologia/`, mais um relatório HTML único (`runs/<run_id>_report.html`) reunindo entrada, cada etapa do determinístico, resultado da LLM e os gráficos ecológicos interativos (ver "Relatório HTML" abaixo).

- **Análise ecológica separada, sobre um CSV revisado à mão**

```bash
python -m harness --ecologia-somente runs/output_pos_curadoria_LLM-2026-09-15.csv --reference data/example/exemplo_1/spp_tradicional.csv --groq-api-key <chave>
```

Depois de revisar `Curated ID` manualmente num CSV já curado por uma execução anterior, roda só a análise ecológica sobre essa versão revisada, sem refazer a curadoria. Gera seu próprio log e seu próprio relatório HTML, mais enxuto (sem tabela de entrada nem resumos do determinístico, já que essa execução não rodou essas etapas, ver "Relatório HTML").

## Relatório HTML

Toda execução que roda a análise ecológica (`--ecologia` ou `--ecologia-somente`) gera também um relatório único em `runs/<run_id>_report.html`, autocontido (sem depender de internet ou de outros arquivos para abrir), com tabelas navegáveis (mas não editáveis) e gráficos interativos.

O relatório sempre descreve só o que aquela execução específica fez, nunca finge ter visto uma etapa que não rodou nela:

- **Execução completa** (`--ecologia`): identificação do projeto e pesquisador, resumo geral dos metadados, tabela de entrada bruta, resumo de cada etapa da curadoria determinística, tabela de saída do determinístico, resultado da curadoria assistida por LLM (mesmo conteúdo do relatório narrativo), tabela final, e os gráficos interativos da análise ecológica.
- **`--ecologia-somente`**: um aviso destacado no topo, deixando claro que essa análise partiu de um CSV já curado por uma execução anterior, possivelmente revisado à mão, sem refazer a curadoria; a tabela usada; e os gráficos interativos.

## Configuração da chave da Groq LLM

A curadoria assistida e o relatório narrativo usam o modelo LLM Groq. Para que funcione, é necessário fornecer uma api-key com `--groq-api-key`:

```bash
python -m harness data/example/exemplo_1/dasafio_Amplo-eDNA_cipo_subset_output-2026-09-13.csv --groq-api-key <chave>
```

A chave será fornecida por e-mail, uma vez que não pode ser disponibilizada em repositórios públicos como o GitHub, sob risco de cancelamento.

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

**`colunas_alias`.** Resolve a diferença entre o nome de coluna que o seu CSV usa e o nome que o sistema espera internamente. Esse segundo nome, o "canônico", é a lista fixa definida em [`data/reference/asv_input_schema.yaml`](data/reference/asv_input_schema.yaml) (`Researcher`, `Project`, `1_subject header`, `Ponto`, `Latitude`, e assim por diante), o mesmo contrato que `tools/schema_validation.py` usa pra checar coluna obrigatória.

Cada par declarado é `nome no seu CSV: nome canônico`. Por exemplo, se a coluna de pesquisador no seu arquivo se chama `Pesquisador` em vez de `Researcher`:

```yaml
colunas_alias:
  Pesquisador: Researcher
```

Isso renomeia a coluna para `Researcher` antes de qualquer outra etapa rodar, inclusive antes da checagem de coluna obrigatória. Dali em diante, o resto do sistema nunca sabe que o nome original era diferente.

- Só é preciso declarar as colunas cujo nome já não bater com o canônico; o resto é assumido como já estando no nome certo.
- Cobre qualquer coluna do contrato: `Researcher`, `Project`, `Primer`, `Sample`, `Unique_File_name`, `ASV absolute abundance`, os campos de cada um dos 3 hits de BLAST (`1_subject header`, `1_staxid`, `1_subject`, `1_indentity`, `1_qcovhsp`, e o mesmo
  para `2_`/`3_`).
- `Ponto` é a única coluna semântica obrigatória. `Latitude`/`Longitude` são opcionais; sem elas, as etapas que dependem de cada uma são puladas com aviso. `Latitude`/`Longitude` esperam grau decimal codificado como inteiro (ex. `-19420314` para `-19.420314`) e são convertidas automaticamente; não há suporte a grau decimal já pronto.

**`contaminacao.fold_change_threshold`.** Limiar do Fold Change (abundância relativa na amostra dividida pela abundância relativa máxima da mesma sequência no controle referenciado) abaixo do qual uma detecção vira `"Possible contamination"`. Default `10`.

**`amplicon_por_primer`.** Faixa de tamanho esperada (pb) por primer, usada para marcar `Primer expected length` e como piso mínimo na árvore filogenética; a chave precisa bater com os valores da coluna `Primer` do CSV. Primer não declarado aqui tem a faixa estimada automaticamente a partir da distribuição de tamanho das próprias sequências daquele primer no dataset (quartis, com margem de 1,5× o intervalo interquartil), avisando quando faz essa estimativa; com poucos dados demais para estimar (menos de 4 sequências), a coluna fica `NA` em vez de arriscar um valor. Default só declara `MiFish2: [140, 200]`.

**`identificacao.pseudoscore_thresholds`.** Limiares do pseudo-score que definem até que nível taxonômico uma sequência é identificada: acima do limiar de `especie`, aceita a identificação do BLAST; senão sobe para `genero`, `familia`, `ordem`, `classe`, nessa ordem; abaixo de todos, vira `"Unidentified"`. Default: `especie: 98, genero: 95, familia: 90, ordem: 80, classe: 60`.

**`arvore_filogenetica.k_vizinhos`.** Quantos vizinhos filogenéticos mais próximos (distância na árvore Neighbor-Joining entre as sequências únicas) entram na coluna `Vizinhos filogenéticos (k)`, evidência usada pela curadoria assistida. Default `5`.

**`taxons_alvo.grupos`.** Lista de grupos ecológicos considerados dentro do escopo para `Possible target taxon`, comparando reino/filo/classe de cada sequência contra os grupos listados. Grupos disponíveis: `Actinopteri` (peixes ósseos), `Metazoa` (reino animal inteiro, mais amplo), `Plantae`, `Benthos`, `Zooplankton`, `Periphyton`, `Phytoplankton`; default `["Actinopteri"]`, calibrado pro dataset de demonstração deste projeto (peixes).

**`checagem_regional`.**

- `fonte`: de onde vêm os registros de ocorrência regional; só `"gbif"` está implementado, outro valor pula a etapa com aviso.
- `buffer_graus`: margem (grau decimal) somada em cada lado do bounding box calculado a partir do min/max de `Latitude`/`Longitude` dos pontos amostrados nos dados. Default `0.1` (~11 km).
- `area_bbox` (opcional): bounding box fixo (`lat_min`, `lat_max`, `long_min`, `long_max`, grau decimal), usado no lugar do cálculo automático quando declarado. Sem `Latitude`/`Longitude` nos dados e sem `area_bbox`, a checagem regional é pulada com aviso.

**`ecologia`.** Usado com `--ecologia` ou `--ecologia-somente` (ver "Execução").

- `coluna_grupo`: coluna usada como unidade espacial (site × taxon) em toda a análise. Default `Ponto`.
- `coluna_id`: identidade de cada ASV usada nas contagens de riqueza/diversidade. Default `Curated ID`.
- `rank_taxonomico`: coluna de rank taxonômico usada no gráfico de composição; qualquer coluna `X (NCBI)` presente na saída serve. Default `Family (NCBI)`.
- `metodo_dissimilaridade`: `bray` (baseado em abundância) ou `jaccard` (presença/ausência), usado na dissimilaridade entre pontos. Default `bray`.
- `min_amostras_curva_acumulacao`: mínimo de amostras (por ponto, ou no total) para tentar uma curva de acumulação; abaixo disso, pulada com aviso em vez de quebrar. Default `2`.
- `metadados_grupo`: colunas de metadado opcional a quebrar riqueza/diversidade por categoria, quando presentes nos dados. Default `[Habitat, Rios]`.

## Formatos de entrada e saída

Entrada e saída são ambos CSV delimitado por `;`, decimal `,`, UTF-8, com aspas no padrão CSV comum. A lista completa de colunas de saída está em [DOMINIO_E_CONTRATO.md](DOMINIO_E_CONTRATO.md), e o contrato de entrada em [`data/reference/asv_input_schema.yaml`](data/reference/asv_input_schema.yaml).

## Rodando os testes

Camada Python (`harness/`):

```bash
pytest tests/ -v
```

Camada R (`r/curadoria_deterministica.R`, ex. o retry do `gbif_regional_count` em limite de taxa do GBIF):

```bash
Rscript r/tests/run_tests.R
```

## Contato

Para mais informações, entre em contato comigo através do meu endereço de [e-mail](mailto:gabrielmendesbrt@outlook.com) 😊
