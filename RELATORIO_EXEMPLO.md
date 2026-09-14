# Relatório de exemplo — execução real sobre o dado de demonstração

```bash
python -m harness data/example/dasafio_Amplo-eDNA_cipo_subset_output-2026-09-13.csv \
  --output data/example/saida_exemplo_curada.csv \
  --reference data/example/spp_tradicional.csv --llm-mode=live
```

CSV completo em [`data/example/saida_exemplo_curada.csv`](data/example/saida_exemplo_curada.csv). O texto
abaixo é o relatório gerado por essa execução (`harness/report_generation.py`), sem edição manual.

---

# Relatório Técnico de Curadoria de Dados eDNA (Metabarcoding) – Peixes

## 1. Contexto da Execução
- **Arquivo de entrada:** `data/example/dasafio_Amplo-eDNA_cipo_subset_output-2026-09-13.csv`
- **Número de linhas de entrada:** 80
- **Janela de execução:** 2026-09-13 21:12:34 UTC → 2026-09-13 21:15:47 UTC

## 2. Resumo Quantitativo

| Métrica | Valor |
|---|---|
| **Identificação determinística** | Species: 62 <br> Unidentified: 16 <br> Genus: 2 |
| **Status de contaminação** | True detection: 80 |
| **Faixa de amplicon esperada (primer)** | In range: 60 <br> Out of range: 20 |
| **Curadoria assistida por LLM** | ASVs únicas revisadas: 26 de 49 <br> Identificações diferentes da determinística: 7 |

## 3. Divergências entre Curadoria Determinística e Assistida por LLM

| ASV | Identificação Determinística | Identificação Assistida (LLM) | Justificativa da Divergência |
|---|---|---|---|
| **ASV_14-173bp** | *Piabarchus* (gênero) | *Piabarchus stramineus* (espécie) – confiança **Alta** | BLAST retornou *Piabarchus stramineus* como melhor hit (pseudo-score 95.82, rank 1). A taxonomia NCBI confirma o gênero *Piabarchus*. A espécie foi registrada por captura tradicional no ponto amostral SC7, reforçando plausibilidade geográfica. Não há indícios de contaminação; a detecção ocorreu em amostra real. |
| **ASV_15-172bp** | *Hypomasticus copelandii* (espécie) | *Hypomasticus* (gênero) – confiança **Média** | BLAST retornou *Hypomasticus copelandii* com pseudo-score 100 e nível de espécie, porém a espécie não possui registro no GBIF para a bacia do São Francisco nem correspondência com espécies capturadas tradicionalmente no ponto SC7. O limite geográfico ou contaminação são possibilidades; o gênero *Hypomasticus* é defensável com confiança média. |
| **ASV_24-168bp** | *Astyanax paranae* (espécie) | *Astyanax* (gênero) – confiança **Média** | BLAST retornou *Astyanax paranae* com pseudo-score 100, indicando alta similaridade. Contudo, a taxonomia NCBI aponta para o gênero *Psalidodon* e não há registros de *A. paranae* no GBIF da bacia. A presença de *Astyanax scabripinnis* em amostras tradicionais do mesmo local apoia a presença de um representante do gênero *Astyanax*, justificando a atribuição ao nível de gênero com confiança média. |
| **ASV_25-168bp** | *Hyphessobrycon anisitsi* (espécie) | *Psalidodon sp.* (gênero) – confiança **Média** | BLAST retornou *Hyphessobrycon anisitsi* com pseudo-score alto (98.38), porém a taxonomia NCBI associada ao ASV indica o gênero *Psalidodon* (família Acestrorhamphidae), gerando conflito. Não há registros de *H. anisitsi* na bacia do São Francisco e a espécie não foi capturada por métodos tradicionais no ponto SC6, reduzindo a confiança na atribuição ao nível de espécie. A designação mais defensável é ao gênero *Psalidodon*, com confiança média. |
| **ASV_34-168bp** | *Hyphessobrycon anisitsi* (espécie) | *Psalidodon* (gênero) – confiança **Média** | BLAST retornou *Hyphessobrycon anisitsi* com pseudo-score 98.38, porém a taxonomia NCBI da sequência indica o gênero *Psalidodon* (família Acestrorhamphidae), gerando conflito. Não há registros de *H. anisitsi* na bacia do São Francisco e a espécie não foi capturada tradicionalmente, assim a identificação mais defensável é ao nível de gênero, com confiança média. |

## 4. Limitações da Execução

- Não foram reportados erros ou etapas puladas na execução descrita.
- As divergências apresentadas refletem limitações inerentes às bases de referência (GBIF, NCBI) e ao alcance geográfico das espécies, o que pode influenciar a confiança nas atribuições taxonômicas.

---

*Este relatório destina-se à cientista de dados responsável pela integração dos resultados ao relatório ambiental.*
