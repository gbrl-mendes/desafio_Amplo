# Domínio e Contrato do Sistema

## O problema / decisão apoiada

Uma empresa de consultoria ambiental é contratada para levantar quais espécies de peixes existem numa área da Serra do Cipó (Minas Gerais), exigência do órgão ambiental como parte do monitoramento de rios e córregos da região. O método usado é DNA ambiental (environmental DNA, eDNA) metabarcoding: em vez de capturar peixes fisicamente, a equipe de campo coleta água em vários pontos do rio e extrai o DNA dissolvido nela, deixado por qualquer organismo que passou por ali. Essas amostras são então enviadas para uma facility de biologia molecular, que extrai o DNA presente nas amostras de água, realiza a amplificação de uma região específica do DNA para identificação taxonômica e por fim, faz o sequenciamento dessas amostras.

A facility então entrega como resultado do sequenciamento para a consultoria uma única tabela, com uma linha para cada trecho de DNA identificado em cada amostra coletada. Cada um desses trechos é chamado de ASV (sigla em inglês para variante de sequência de amplicon): uma sequência específica de DNA, curta, que apareceu um número mínimo de vezes na leitura da amostra para não ser considerada erro do sequenciador. Para cada sequência, a tabela já traz as três espécies mais parecidas encontradas num banco de referência genético, com metadados de qualidade associados a essa identificação (percentual de identidade e de cobertura do alinhamento com a referência).

Esse dado bruto vem com problemas conhecidos do método: contaminação de laboratório (DNA humano, de animal doméstico, de outra amostra processada no mesmo lote), amplificação de DNA de espécies fora do grupo de interesse, e casos em que a espécie mais parecida no banco de referência não tem qualidade suficiente para confiar naquele resultado.

A pessoa responsável por transformar essa tabela bruta numa base de dados confiável, dentro da consultoria, é a cientista de dados. Cabe a ela decidir, para cada sequência de DNA da tabela:

1. Qual identificação taxonômica ela sustenta, e até que nível (espécie, gênero, família...), sem afirmar mais do que a evidência permite.
2. Se ela representa a detecção real de um organismo ou uma provável contaminação, comparando com os controles negativos processados junto com as amostras.
3. Se o tamanho da sequência é compatível com o marcador genético usado no sequenciamento.
4. Quando essas três decisões não dão uma resposta clara sozinhas, uma segunda camada, com apoio de um modelo de linguagem, avalia a plausibilidade biológica e geográfica da identificação usando evidência adicional: sequências parecidas dentro do próprio lote, registros de ocorrência pública da espécie na região (GBIF), e espécimes já coletados fisicamente na mesma área em campanhas anteriores, quando disponível para aquele ponto específico (segundo input opcional, formato descrito em Domínio e família de dados).

O resultado é uma base de dados curada: a identificação de cada sequência, por ponto de coleta, com o nível de confiança de cada identificação e a evidência que a sustenta. É essa base que embasa o relatório técnico entregue ao órgão ambiental.

## O usuário do resultado

A cientista de dados da consultoria responsável por transformar a tabela bruta entregue pela facility de sequenciamento numa base de dados confiável, antes de essa base entrar no relatório técnico enviado ao órgão ambiental. Hoje esse trabalho é feito manualmente, sequência por sequência, decidindo o que é ruído, contaminação ou identificação válida. O sistema organiza a evidência disponível (resultado do BLAST, sequências parecidas dentro do próprio lote, registros de ocorrência da espécie na região) num formato que essa pessoa revisa mais rápido e com menos chance de erro.

## Domínio e família de dados

Monitoramento de biodiversidade de peixes via metabarcoding de eDNA. Dados de demonstração: projeto `eDNA_Cipo`, já publicado (Valentine, 2025), marcador genético MiFish2 (região do gene 12S do DNA mitocondrial), 7 pontos amostrais na Serra do Cipó, bacia do rio São Francisco (MG, Brasil), 21 amostras reais mais 2 controles (extração e filtragem).

**Formato de entrada esperado:** tabela em formato *long* (uma linha por combinação de sequência e amostra), saída típica de um pipeline DADA2 seguido de BLAST.

- CSV delimitado por `;`, decimal `,` (inclusive em notação científica, ex. `1,92e-80`), UTF-8, aspas padrão CSV.
- Contrato completo de colunas (obrigatórias, opcionais, e as que nunca podem estar presentes) em [`data/reference/asv_input_schema.yaml`](data/reference/asv_input_schema.yaml).
- Chave primária composta: `ASV (Sequence)` mais `Unique_File_name` (a mesma sequência se repete em várias amostras).
- Cada amostra referencia seus controles diretamente pelas colunas `Ext. Control`, `PCR Control` e `Filt. Control` (nome do `Unique_File_name` do controle correspondente).

O sistema é parametrizado (`--config`), não fixo para este dataset. Outro marcador genético, outro projeto de eDNA de peixes, ou outro grupo ecológico (o registro de grupos-alvo em `TARGET_TAXA_REGISTRY` já cobre bentos, zooplâncton, fitoplâncton e perifíton, além de peixes) pode reaproveitar a mesma arquitetura ajustando o arquivo de configuração.

**Segundo input, opcional:** tabela de espécies obtidas por métodos tradicionais de monitoramento (captura física, não eDNA) nos mesmos pontos amostrais, usada só como evidência adicional na camada assistida por LLM (decisão 4 acima) — nunca entra na curadoria determinística. CSV `;`-delimitado, UTF-8, uma linha por combinação espécie×ponto, colunas `Ponto` e `Taxon_binomial`; exemplo em [`data/example/spp_tradicional.csv`](data/example/spp_tradicional.csv). Repassado via `--reference` (ver Ponto de entrada e execução). Sem essa tabela, a curadoria assistida roda normalmente, só sem essa evidência extra.

## Saídas previstas

Um CSV final (mesmo formato de entrada), com as colunas reordenadas para compatibilidade com o formato histórico do projeto (colunas em comum na mesma posição; colunas renomeadas tratadas como equivalentes; colunas novas no final, ver `finalize_output_columns` no `curadoria_deterministica.qmd`), contendo:

- **Determinísticas** (R): `BLAST ID`, `Identification`, `Identification Max. taxonomy`, `BLASTn pseudo-score`, `Contamination status`, `Primer expected length`, `Possible target taxon`, taxonomia NCBI completa (Gênero a Superreino), `Vizinhos filogenéticos (k)`, `GBIF regional occurrence count`, `FC to Ext/Filt control`, `Control presence`.
- **Assistidas por LLM** (Python + Groq, opcional): `Assisted ID (LLM)`, `Assisted Confidence (LLM)`, `Assisted Justification (LLM)`, preenchidas apenas para as sequências cuja identificação determinística não foi conclusiva. Nunca sobrescrevem as colunas acima.
- Um **log JSON por execução** (`runs/<timestamp>.json`): status (recusado, sucesso ou falha), resumo da validação, o que a curadoria assistida revisou, avisos e erros. Serve para reconstruir depois o que foi feito e por quê, sem precisar reexecutar nada.
- Um **relatório narrativo por execução** (`runs/<timestamp>_relatorio.md`, opcional, mesmo interruptor `--llm-mode`): síntese em markdown do que essa execução encontrou (contexto, contagens por categoria, casos concretos em que a curadoria assistida divergiu da determinística, limitações). Gerado por LLM sobre agregados já calculados em Python (`harness/report_generation.py`) — nunca lê a tabela bruta nem inventa número fora do que foi computado.

## Fora de escopo

- **Determinar a "verdade" definitiva de uma identificação:** o sistema reporta confiança e justificativa, nunca certeza absoluta. Divergências encontradas entre diferentes fontes reais usadas para calibrar o sistema foram documentadas, não escondidas sob uma resposta forçada. Cabe aos profissionais que analisarem esses dados a decisão final sobre quais identificações são as mais parcimoniosas.
- **Análise ecológica downstream** (diversidade, comparação entre pontos, mapas): este pacote entrega a tabela curada; a interpretação ecológica é uma etapa posterior e separada, por decisão de arquitetura (módulos com família de dados e pergunta diferentes).
- **Dado fora da família declarada**, sem ajuste de configuração: por exemplo outro tipo de marcador genético, outro grupo taxonômico alvo sem declarar o grupo correspondente em `taxons_alvo`, ou faixa de tamanho de sequência de outro marcador sem declarar em `amplicon_por_primer`.
- **Gabarito de curadoria humana** (`Curated ID` / `Obs. Curadoria`): nunca é produzido pelo sistema nem aceito como entrada. É comparado externamente, fora deste repositório.

## Principais riscos e mecanismos de validação

| Risco | Mecanismo de mitigação |
|---|---|
| Vazamento de gabarito ou de colunas derivadas por outro pipeline | `tools/schema_validation.py` recusa a entrada (bloqueante) se colunas `derived_recompute`/`gabarito` estiverem presentes. |
| Entrada com colunas obrigatórias ausentes, chave primária duplicada | Mesma validação, com relatório específico de quais colunas e linhas falharam. A execução é recusada antes de rodar qualquer coisa determinística. |
| Bug documentado do pipeline de referência (múltiplos controles numa célula separados por `;`) | Validado explicitamente como aviso, não bloqueante, sinalizado no relatório em vez de quebrar silenciosamente um cruzamento de dados. |
| Dependência externa ausente ou instável (R, pacotes, NCBI, GBIF, Groq) | Degradação graciosa em cada camada: o harness reporta `failed` com motivo claro se o R não roda; a curadoria assistida por LLM cai para `off` sozinha sem `GROQ_API_KEY`, sem derrubar o resultado determinístico já validado. |
| Resultado do R incompleto ou corrompido apesar de exit code 0 | Verificação pós-execução (`verify_output`): confere número de linhas e presença das colunas centrais antes de declarar sucesso. |
| Catálogo de modelo de LLM muda (a Groq já descontinuou o modelo usado inicialmente durante o desenvolvimento) | Modelo e chave são configuráveis via `.env` ou variável de ambiente, não fixos na lógica; falha numa consulta específica não derruba as demais (nova tentativa com espera progressiva, erro registrado individualmente). |
| Confiar demais na camada de LLM | A skill só é acionada para sequências cuja identificação determinística não foi conclusiva (não chegou a espécie, hit não confiável, ou espécie sem registro regional no GBIF). Identificações já confiáveis nunca passam pelo LLM, e a saída sempre cita a evidência determinística usada no raciocínio. |
| Parâmetros de curadoria (faixa de tamanho de sequência, limiares de pseudo-score, número de vizinhos na árvore) sem calibração real | Fechados contra dados reais do projeto (árvore filogenética e tabela de curadoria do orientador, TCC da Isadora) em vez de estimados. Ver notas de arquitetura para o histórico de calibração. |
| Lista de espécimes de campanhas anteriores incompleta ou sem cobertura em todos os pontos | Tratada como evidência a favor, nunca como lista fechada; ausência de registro num ponto sem cobertura tradicional (ex. um ponto sem nenhum espécime catalogado) não é tratada como ausência da espécie, só como falta de dado de comparação. |

## Ponto de entrada e execução

Ver [README](README.md) para instalação, dependências e exemplo de execução. Em resumo:

```bash
python -m harness data/example/dasafio_Amplo-ASVs_BLASTr_output-2026-09-12.csv --output saida.csv --reference data/example/spp_tradicional.csv
```