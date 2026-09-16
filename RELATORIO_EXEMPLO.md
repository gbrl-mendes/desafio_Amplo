# Relatório de exemplo: execução real sobre o dado de demonstração

```bash
python -m harness data/example/exemplo_1/dasafio_Amplo-eDNA_cipo_subset_output-2026-09-13.csv \
  --output data/example/exemplo_1/saida_exemplo_curada.csv \
  --reference data/example/exemplo_1/spp_tradicional.csv --llm-mode=live
```

CSV completo em [`data/example/exemplo_1/saida_exemplo_curada.csv`](data/example/exemplo_1/saida_exemplo_curada.csv). O relatório narrativo gerado por essa execução (`harness/report_generation.py`, sem edição manual), renderizado em PDF (tema Cayman, `harness/pdf_report.py`), está em [`RELATORIO_EXEMPLO.pdf`](RELATORIO_EXEMPLO.pdf).
