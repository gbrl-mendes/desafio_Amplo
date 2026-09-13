# Desafio Amplo

Este projeto implementa minha resposta ao desafio técnico do processo seletivo para a vaga de Cientista de Dados Pleno na Amplo Engenharia, desenvolvido segundo os parâmetros pré-definidos para os concorrentes.

O problema resolvido, descrito em detalhes em [DOMINIO_E_CONTRATO.md](DOMINIO_E_CONTRATO.md), é hipotético: uma consultoria ambiental recebe uma tabela bruta de sequências de DNA ambiental e precisa transformá-la numa base de dados confiável sobre quais espécies existem numa área de estudo. Este é o sistema que a cientista de dados dessa consultoria executaria para resolver esta demanda. Ele identifica cada sequência taxonomicamente, separa detecção real de contaminação, confere se o tamanho é compatível com o marcador genético usado, e, para os casos que essas três etapas não resolvem sozinhas, consulta um modelo de linguagem (LLM) com a evidência já calculada para uma segunda opinião. O resultado dessa segunda opinião nunca sobrescreve o resultado determinístico, entra como colunas adicionais. No final, o profissional humano ainda tem a possibilidade de definir, com base em tudo que foi gerado, quais são as identificações mais parcimoniosas.

Veja [DOMINIO_E_CONTRATO.md](DOMINIO_E_CONTRATO.md) para o problema, o domínio, os formatos e as limitações declaradas deste sistema, e [RELATORIO_EXEMPLO.md](RELATORIO_EXEMPLO.md) para uma execução real e completa sobre o dataset original, com achados concretos. As decisões de arquitetura e a calibração de cada parâmetro estão documentadas nos comentários do próprio [`r/curadoria_deterministica.qmd`](r/curadoria_deterministica.qmd), seção a seção.

## Arquitetura

| Arquivo                                                            | Função                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`tools/schema_validation.py`](tools/schema_validation.py)         | Valida o contrato de entrada (Python). Recusa a execução antes de rodar qualquer coisa se faltar coluna obrigatória, houver vazamento de gabarito, ou a chave primária for duplicada.                                                                                                                                                                                                                                                                                                                            |
| [`r/curadoria_deterministica.qmd`](r/curadoria_deterministica.qmd) | Toda a lógica determinística (R): leitura do CSV, refinamento dos três melhores hits de BLAST (busca por similaridade de sequência num banco de referência), consulta de taxonomia ao NCBI, checagem de contaminação contra os controles, faixa de tamanho esperada por marcador genético, pseudo-score (nota de confiança combinando identidade e cobertura do alinhamento), árvore filogenética entre as sequências, e checagem de ocorrência regional no GBIF (banco público de registros de biodiversidade). |
| [`r/curadoria_deterministica.R`](r/curadoria_deterministica.R)     | Versão executável, gerada do `.qmd` acima. É o arquivo que o harness realmente chama, como um subprocesso separado.                                                                                                                                                                                                                                                                                                                                                                                              |
| [`harness/orchestrator.py`](harness/orchestrator.py)               | Integração de todas as etapas: validação da entrada, execução do pipeline em R, verificação do output do R, chamada da curadoria assistida por LLM, geração do relatório narrativo, e gravação do log em JSON de cada execução.                                                                                                                                                                                                                                                                                  |
| [`harness/llm_curation.py`](harness/llm_curation.py)               | Curadoria assistida por LLM, via Groq.                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| [`harness/report_generation.py`](harness/report_generation.py)     | Relatório narrativo da execução, também via Groq, a partir dos agregados gerados nas etapas anteriores.                                                                                                                                                                                                                                                                                                                                                                                                          |
| [`harness/__main__.py`](harness/__main__.py)                       | Ponto de entrada da linha de comando (`python -m harness`).                                                                                                                                                                                                                                                                                                                                                                                                                                                      |

## Requisitos

- **Python 3.11+**, de preferência num ambiente virtual (`pip install -r requirements.txt`).
- **R 4.x** com os pacotes listados em [`r/install_packages.R`](r/install_packages.R). Rode uma vez: `Rscript r/install_packages.R` (inclui tidyverse, yaml, taxize, ape, rgbif e, via Bioconductor, DECIPHER/Biostrings).
- Acesso à internet durante a execução: as consultas ao NCBI (taxonomia) e ao GBIF (ocorrência regional) fazem parte do pipeline determinístico, não só da parte de LLM.
- Opcional: uma chave de API gratuita da [Groq](https://console.groq.com/keys) para a curadoria assistida e o relatório narrativo. Já incluída em `.env` para facilitar a avaliação deste desafio (ver a nota de segurança no final deste documento). Sem chave, as duas etapas são puladas automaticamente e o resto do pipeline roda normalmente.

## Instalação

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac
pip install -r requirements.txt
Rscript r/install_packages.R
```

## Execução

```bash
python -m harness <entrada.csv> [--config config.yaml] [--output saida.csv] [--runs-dir runs/] [--llm-mode live|mock|off] [--reference spp_tradicional.csv]
```

- `<entrada.csv>`: obrigatório. Schema completo em [`data/reference/asv_input_schema.yaml`](data/reference/asv_input_schema.yaml).
- `--config`: YAML opcional, sobrescreve os parâmetros determinísticos padrão (limiar de detecção de contaminação, faixa de tamanho esperada por marcador genético, limiares de pseudo-score, número de vizinhos filogenéticos considerados, área regional consultada no GBIF). Fica registrado no log da execução, para rastreabilidade.
- `--output`: caminho do CSV final. Por padrão, `runs/<nome-da-entrada>_curado.csv`.
- `--runs-dir`: pasta onde gravar o log de cada execução. Por padrão, `runs/`.
- `--llm-mode`: um único parâmetro para as duas etapas que usam a Groq, a curadoria assistida (`harness/llm_curation.py`) e o relatório narrativo (`harness/report_generation.py`):
  - `live` (padrão): chama a Groq nas duas etapas. Preenche as colunas `Assisted ID/Confidence/Justification (LLM)` nas sequências revisadas e grava um relatório de verdade em `runs/<timestamp>_relatorio.md`. Sem `GROQ_API_KEY` configurada (`.env` ou variável de ambiente), as duas etapas são puladas sozinhas, sai só o CSV determinístico e o log JSON, sem quebrar a execução.
  - `mock`: não chama a Groq. Preenche as mesmas colunas e o mesmo arquivo de relatório, mas com uma resposta simulada fixa, servindo apenas pra testar o encadeamento sem gastar cota de API.
  - `off`: pula as duas etapas por completo. Sai só o CSV resultado do pipeline em R (`curadoria_deterministica.R`) e o log JSON, sem colunas assistidas e sem relatório narrativo.
- `--reference`: CSV opcional (delimitado por `;`, UTF-8, colunas `Ponto` e `Taxon_binomial`) com espécies já registradas por métodos tradicionais de monitoramento (captura física, identificação morfológica) para os mesmos pontos amostrais. Usado como evidência adicional na curadoria assistida por LLM (ver [DOMINIO_E_CONTRATO.md](DOMINIO_E_CONTRATO.md)). Exemplo em [`data/example/spp_tradicional.csv`](data/example/spp_tradicional.csv).

Código de saída do processo: `0` sucesso, `2` entrada recusada pela validação, `3` falha na execução.

### Exemplos

- **Uso simples**

```bash
python -m harness data/example/dasafio_Amplo-eDNA_cipo_subset_output-2026-09-13.csv --output saida.csv
```

O dado de demonstração usado aqui é um subset randomizado de 50 sequências (80 linhas, sequência × amostra) do dataset original do projeto eDNA_Cipo (815 linhas), reduzido para manter o consumo de cota de API e o tempo de execução baixos ao demonstrar o pipeline. Esse comando grava `saida.csv`, um log em `runs/<timestamp>.json`, e um relatório narrativo em `runs/<timestamp>_relatorio.md`.

- **Uso com referência de amostragens tradicionais**

```bash
python -m harness data/example/dasafio_Amplo-eDNA_cipo_subset_output-2026-09-13.csv --reference data/example/spp_tradicional.csv --output saida.csv
```

Mesma execução anterior, mas a curadoria assistida também considera, para cada ponto de coleta, quais espécies já foram registradas ali por metodologias tradicionais, a partir da tabela fornecida no parâmetro `--reference`.

## Formatos de entrada e saída

Entrada e saída são ambos CSV delimitado por `;`, decimal `,`, UTF-8, com aspas no padrão CSV comum. A lista completa de colunas de saída está em [DOMINIO_E_CONTRATO.md](DOMINIO_E_CONTRATO.md), e o contrato de entrada em [`data/reference/asv_input_schema.yaml`](data/reference/asv_input_schema.yaml).

## Rodando os testes

```bash
pytest tests/ -v
```

37 testes, cobrindo `tools/schema_validation.py`, `harness/orchestrator.py`, `harness/llm_curation.py` e `harness/report_generation.py`. Todos usam substitutos falsos no lugar de dependências externas (um R falso, uma chamada de LLM simulada) e não precisam de R instalado, acesso à rede, nem uma chave de API real para rodar.

# 

## Contato

Para mais informações, entre em contato comigo através do meu endereço de [e-mail](mailto:gabrielmendesbrt@outlook.com) 😊
