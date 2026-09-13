# Domínio e Contrato do Sistema

## O problema / decisão apoiada

Dado um lote de sequenciamento de eDNA (environmental DNA) já processado por DADA2 e BLAST
(top-3 hits por ASV), decidir, para cada ASV (Amplicon Sequence Variant):

1. Qual identificação taxonômica ela sustenta, e até que nível (espécie, gênero, família...) —
   não além do que a evidência realmente permite.
2. Se ela representa uma detecção real ou uma provável contaminação (comparando com os
   controles de extração/PCR/filtragem do próprio lote).
3. Se o tamanho do amplicon é compatível com o primer usado.
4. Quando a identificação determinística fica incerta, uma segunda camada (assistida por LLM)
   julga plausibilidade biológica/geográfica usando evidência adicional (vizinhos
   filogenéticos, registros regionais no GBIF) — sem nunca substituir o resultado
   determinístico, só complementando com confiança e justificativa auditáveis.

O sistema não decide "isto é biodiversidade real do local" de forma definitiva — ele produz uma
curadoria consistente, verificável e rastreável que **apoia** essa decisão humana.

## O usuário do resultado

Um(a) pesquisador(a) ou curador(a) de projetos de eDNA que precisa revisar um lote de ASVs
antes de reportar resultados de biodiversidade — alguém com conhecimento de domínio
(taxonomia, ecologia), não um usuário final leigo. O sistema produz evidência organizada para
essa pessoa decidir mais rápido e com menos chance de erro, não substitui o julgamento dela.

## Domínio e família de dados

Monitoramento de biodiversidade de peixes via metabarcoding de eDNA. Dado de demonstração:
projeto `eDNA_Cipo`, primer **MiFish2** (12S mitocondrial), 7 pontos amostrais na Serra do
Cipó / bacia do rio São Francisco (MG, Brasil), 21 amostras reais + 2 controles (extração e
filtragem).

**Formato de entrada esperado:** tabela em formato *long* (uma linha por combinação
ASV × amostra), saída típica de um pipeline DADA2 → BLASTr:

- CSV delimitado por `;`, decimal `,` (inclusive em notação científica, ex. `1,92e-80`),
  UTF-8, aspas padrão CSV.
- Contrato completo de colunas (obrigatórias, opcionais, e as que nunca podem estar
  presentes) em [`data/reference/asv_input_schema.yaml`](data/reference/asv_input_schema.yaml).
- Chave primária **composta**: `ASV (Sequence)` + `Unique_File_name` (a mesma ASV se repete
  em várias amostras).
- Cada amostra referencia seus controles diretamente pelas colunas `Ext. Control` /
  `PCR Control` / `Filt. Control` (nome do `Unique_File_name` do controle correspondente).

O sistema é parametrizado (`--config`), não hardcoded para este dataset — outro
primer/projeto de eDNA de peixes ou outro domínio ecológico (o registro de grupos-alvo em
`TARGET_TAXA_REGISTRY` já cobre bentos, zooplâncton, fitoplâncton e perifíton, além de
Metazoa) pode reaproveitar a mesma arquitetura ajustando o YAML de configuração.

## Saídas previstas

Um CSV final (mesmo formato de entrada), com as colunas reordenadas para compatibilidade com
o output "long" histórico do projeto (colunas em comum na mesma posição; colunas renomeadas
tratadas como equivalentes; colunas novas no final — ver `finalize_output_columns` no
`curadoria_deterministica.qmd`), contendo:

- **Determinísticas** (R): `BLAST ID`, `Identification`, `Identification Max. taxonomy`,
  `BLASTn pseudo-score`, `Contamination status`, `Primer expected length`,
  `Possible target taxon`, taxonomia NCBI completa (Gênero→Superreino), `Vizinhos
  filogenéticos (k)`, `GBIF regional occurrence count`, `FC to Ext/Filt control`,
  `Control presence`.
- **Assistidas por LLM** (Python + Groq, opcional): `Assisted ID (LLM)`,
  `Assisted Confidence (LLM)`, `Assisted Justification (LLM)` — só preenchidas para ASVs cuja
  identificação determinística não foi conclusiva; nunca sobrescrevem as colunas acima.
- Um **log JSON por execução** (`runs/<timestamp>.json`): status (recusado/sucesso/falha),
  resumo da validação, o que a curadoria assistida revisou, avisos e erros — para reconstruir
  depois o que foi feito e por quê, sem precisar reexecutar nada.

## Fora de escopo

- **Clustering OTU/SWARM** — decisão explícita de excluir (ver `role: out_of_scope` no
  contrato de schema).
- **Determinar a "verdade" definitiva de uma identificação** — o sistema reporta confiança e
  justificativa, nunca certeza absoluta; discrepâncias entre fontes de calibração reais deste
  projeto (ex. divergência de identificação entre a árvore/tabela do orientador e o texto do
  TCC para a mesma ASV) foram documentadas, não escondidas sob uma resposta forçada.
- **Análise ecológica downstream** (diversidade, comparação entre pontos/estações, mapas) —
  este pacote entrega a tabela curada; a interpretação ecológica é uma etapa posterior e
  separada por decisão de arquitetura (módulos com família de dados e pergunta diferentes).
- **Dado fora da família declarada** sem ajuste de configuração — ex. outro tipo de
  marcador/sequenciamento, outro grupo taxonômico alvo sem declarar o grupo correspondente em
  `taxons_alvo`, ou faixa de amplicon de outro primer sem declarar em `amplicon_por_primer`.
- **Gabarito de curadoria humana** (`Curated ID` / `Obs. Curadoria`) nunca é produzido pelo
  sistema nem aceito como entrada — é comparado externamente, fora deste repositório.

## Principais riscos e mecanismos de validação

| Risco | Mecanismo de mitigação |
|---|---|
| Vazamento de gabarito ou de colunas derivadas por outro pipeline | `tools/schema_validation.py` recusa a entrada (bloqueante) se colunas `derived_recompute`/`gabarito` estiverem presentes. |
| Entrada com colunas obrigatórias ausentes, chave primária duplicada | Mesma validação, com relatório específico de quais colunas/linhas falharam — a execução é recusada antes de rodar qualquer coisa determinística. |
| Bug documentado do pipeline de referência (múltiplos controles numa célula separados por `;`) | Validado explicitamente como aviso (não bloqueante), sinalizado no relatório em vez de quebrar silenciosamente um join. |
| Dependência externa ausente/instável (R, pacotes, NCBI, GBIF, Groq) | Degradação graciosa em cada camada: harness reporta `failed` com motivo claro se o R não roda; curadoria assistida por LLM cai para `off` sozinha sem `GROQ_API_KEY`, sem derrubar o resultado determinístico já validado. |
| Resultado do R incompleto/corrompido apesar de exit code 0 | Verificação pós-execução (`verify_output`): confere número de linhas e presença das colunas centrais antes de declarar sucesso. |
| Catálogo de modelo de LLM muda (a Groq já descontinuou o modelo usado inicialmente durante o desenvolvimento) | Modelo e chave são configuráveis via `.env`/variável de ambiente, não hardcoded na lógica; falha de uma ASV específica na consulta não derruba as demais (retry com backoff, erro registrado por ASV). |
| Confiar demais na camada de LLM | A skill só é acionada para ASVs cuja identificação determinística não foi conclusiva (não-espécie, hit não confiável, ou espécie sem registro regional no GBIF) — identificações já confiáveis nunca passam pelo LLM; a saída sempre cita a evidência determinística usada no raciocínio. |
| Parâmetros de curadoria (faixa de amplicon, limiares de pseudo-score, k de vizinhos) sem calibração real | Fechados contra dados reais do projeto (árvore filogenética e tabela de curadoria do orientador, TCC da Isadora) em vez de estimados — ver notas de arquitetura para o histórico de calibração. |

## Ponto de entrada e execução

Ver [README](README.md) para instalação, dependências e exemplo de execução. Em resumo:

```bash
python -m harness data/example/dasafio_Amplo-ASVs_BLASTr_output-2026-09-12.csv --output saida.csv
```
