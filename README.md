# Curadoria taxonômica de ASVs (eDNA metabarcoding)

Sistema que recebe uma tabela de ASVs pós-DADA2/BLAST, curadoria taxonomicamente cada uma
(identificação, contaminação, faixa de amplicon, árvore filogenética, checagem regional) e,
para os casos que a curadoria determinística não resolveu sozinha, consulta um LLM com a
evidência já calculada para uma segunda opinião — sem nunca sobrescrever o resultado
determinístico.

Ver [DOMINIO_E_CONTRATO.md](DOMINIO_E_CONTRATO.md) para o problema, domínio, formatos, saídas
e limitações declaradas deste sistema, e [RELATORIO_EXEMPLO.md](RELATORIO_EXEMPLO.md) para uma
execução real, completa, sobre o dado de demonstração (com achados concretos, inclusive um
limite de cota do LLM atingido durante essa própria execução). As decisões de arquitetura e a
calibração de cada parâmetro estão documentadas nos comentários do próprio
[`r/curadoria_deterministica.qmd`](r/curadoria_deterministica.qmd), seção a seção.

## Arquitetura, em uma frase por peça

| Peça | Papel |
|---|---|
| [`tools/schema_validation.py`](tools/schema_validation.py) | Valida o contrato de entrada (Python) — recusa antes de rodar qualquer coisa se faltar coluna obrigatória, houver vazamento de gabarito, ou a chave primária for duplicada. |
| [`r/curadoria_deterministica.qmd`](r/curadoria_deterministica.qmd) | Toda a lógica determinística (R): parsing, BLAST, taxonomia NCBI, contaminação, faixa de amplicon, pseudo-score, árvore filogenética, checagem regional GBIF. Fonte de verdade — o `.R` é gerado a partir dele (`knitr::purl`). |
| [`r/curadoria_deterministica.R`](r/curadoria_deterministica.R) | Versão executável (gerada) do `.qmd` acima — é o que o harness realmente chama via subprocesso. |
| [`harness/orchestrator.py`](harness/orchestrator.py) | Conecta tudo: valida → roda o R → verifica a saída → curadoria assistida por LLM → grava um log JSON por execução. |
| [`harness/llm_curation.py`](harness/llm_curation.py) | Curadoria assistida por LLM (Groq, gratuito) — só para ASVs cuja identificação determinística não foi conclusiva. |
| [`harness/__main__.py`](harness/__main__.py) | Ponto de entrada da linha de comando (`python -m harness`). |

## Requisitos

- **Python 3.11+** com um venv (`pip install -r requirements.txt`).
- **R 4.x** com os pacotes em [`r/install_packages.R`](r/install_packages.R) (rode uma vez:
  `Rscript r/install_packages.R` — inclui tidyverse, yaml, taxize, ape, rgbif e, via
  Bioconductor, DECIPHER/Biostrings).
- Acesso à internet durante a execução (consultas ao NCBI e ao GBIF são parte do pipeline
  determinístico).
- Opcional: uma chave de API gratuita da [Groq](https://console.groq.com/keys) para a
  curadoria assistida por LLM (já incluída em `.env` para facilitar a avaliação deste
  desafio — ver a nota de segurança abaixo). Sem chave, essa etapa é pulada automaticamente
  e o resto do pipeline roda normalmente.

## Instalação

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac
pip install -r requirements.txt
Rscript r/install_packages.R
```

## Modo de execução

```bash
python -m harness <entrada.csv> [--config config.yaml] [--output saida.csv] [--llm-mode live|mock|off]
```

- `<entrada.csv>`: obrigatório. Schema em
  [`data/reference/asv_input_schema.yaml`](data/reference/asv_input_schema.yaml).
- `--config`: YAML opcional sobrescrevendo os parâmetros determinísticos padrão (limiar de
  Fold Change, faixa de amplicon por primer, limiares de pseudo-score, k de vizinhos
  filogenéticos, área regional do GBIF). Registrado no log do run para rastreabilidade.
- `--output`: caminho do CSV final (default: `runs/<nome-da-entrada>_curado.csv`).
- `--llm-mode`: `live` (default; usa a chave em `.env`, pula sozinho se não houver chave),
  `mock` (simula a resposta, sem rede — útil pra inspecionar o mecanismo sem gastar cota) ou
  `off` (pula por completo).

Código de saída: `0` sucesso, `2` entrada recusada pela validação, `3` falha de execução.

### Exemplo simples

```bash
python -m harness data/example/dasafio_Amplo-ASVs_BLASTr_output-2026-09-12.csv --output saida.csv
```

Roda a curadoria completa sobre o dado de demonstração (815 ASVs×amostra, eDNA_Cipo,
MiFish2) e grava `saida.csv` + um log em `runs/`.

## Formatos de entrada e saída

Ambos CSV `;`-delimitado, decimal `,`, UTF-8, aspas padrão. Ver
[DOMINIO_E_CONTRATO.md](DOMINIO_E_CONTRATO.md) para a lista completa de colunas de saída e o
contrato de entrada em [`data/reference/asv_input_schema.yaml`](data/reference/asv_input_schema.yaml).

## Rodando os testes

```bash
pytest tests/ -v
```

24 testes, de `tools/schema_validation.py`, `harness/orchestrator.py` e
`harness/llm_curation.py` — todos usam duplos falsos (R falso, chamada de LLM
falsa/mockada) e nunca dependem de R instalado, rede ou uma chave de API real.

## Nota de segurança sobre a chave em `.env`

Este repositório está **privado** e a chave da Groq incluída em `.env` foi deixada de
propósito, por decisão do autor, para minimizar fricção de quem for avaliar este desafio (sem
precisar criar conta própria). Uma variável de ambiente `GROQ_API_KEY` já definida no sistema
tem prioridade sobre o valor do arquivo, então quem preferir usar a própria chave pode
sobrescrever sem editar nada.
