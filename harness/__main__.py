"""Ponto de entrada do harness: `python -m harness <entrada.csv> [opcoes]`.

Ve `orchestrator.run` para o fluxo completo (resumo da entrada -> validar ->
rodar R -> verificar -> checkpoint -> curadoria assistida -> logar). Este
modulo so cuida de argumentos de linha de comando e do codigo de saida do
processo, que reflete o status do run:
  0 = sucesso, 2 = entrada recusada pela validacao, 3 = falha de execucao
  (R ausente, erro no script R, verificacao pos-execucao reprovada, etc.).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness.orchestrator import DEFAULT_RUNS_DIR, run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m harness",
        description="Roda a curadoria deterministica de ASVs (eDNA_Cipo, MiFish2) sobre um CSV de entrada.",
    )
    parser.add_argument("input_csv", help="Caminho do CSV de entrada (schema em data/reference/asv_input_schema.yaml).")
    parser.add_argument(
        "--config",
        dest="config_path",
        default=None,
        help="YAML opcional sobrescrevendo os parametros deterministicos padrao (registrado no log do run).",
    )
    parser.add_argument(
        "--output",
        dest="output_csv",
        default=None,
        help="Caminho do CSV de saida (default: runs/output_pos_curadoria_LLM-<AAAA-MM-DD>.csv).",
    )
    parser.add_argument(
        "--runs-dir",
        default=str(DEFAULT_RUNS_DIR),
        help="Pasta onde gravar o log JSON de cada run (default: runs/).",
    )
    parser.add_argument(
        "--llm-mode",
        choices=["live", "mock", "off"],
        default="live",
        help=(
            "Curadoria assistida por LLM apos a determinística: 'live' (default; chama a Groq, "
            "degrada sozinho pra 'off' se GROQ_API_KEY nao estiver configurada), 'mock' (simula "
            "a resposta, sem rede, so testa o encadeamento) ou 'off' (pula por completo)."
        ),
    )
    parser.add_argument(
        "--reference",
        dest="traditional_species_csv",
        default=None,
        help=(
            "CSV opcional (;-delimitado, UTF-8, pelo menos 2 colunas: ponto na primeira, "
            "taxon na ultima ou numa coluna chamada Taxon_binomial) com especies obtidas por "
            "metodos tradicionais de monitoramento -- usado como evidencia extra na curadoria "
            "assistida por LLM. Ver data/example/exemplo_1/spp_tradicional.csv."
        ),
    )
    parser.add_argument(
        "--yes",
        "-y",
        dest="assume_yes",
        action="store_true",
        help=(
            "Pula a pergunta do checkpoint entre a curadoria deterministica e a assistida por "
            "LLM, prosseguindo direto com o --llm-mode configurado. Sem essa flag, num terminal "
            "interativo com --llm-mode=live a execucao para nesse ponto e pergunta."
        ),
    )
    parser.add_argument(
        "--ecologia",
        action="store_true",
        help=(
            "Roda a analise ecologica (r/analise_ecologica.R) logo apos a curadoria assistida, "
            "sobre a coluna Curated ID do CSV final. Escreve tabelas e graficos em "
            "runs/<timestamp>_ecologia/. Desligada por padrao."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    result = run(
        input_csv=args.input_csv,
        config_path=args.config_path,
        output_csv=args.output_csv,
        runs_dir=args.runs_dir,
        llm_mode=args.llm_mode,
        traditional_species_csv=args.traditional_species_csv,
        assume_yes=args.assume_yes,
        ecologia=args.ecologia,
    )

    print(f"Status: {result.status}")
    print(result.validation_summary)
    if result.validation_warnings:
        print("Avisos de validacao:")
        for w in result.validation_warnings:
            print(f"  - {w}")

    if result.status == "refused":
        print("\nEntrada recusada -- corrija os problemas bloqueantes acima e rode novamente.")
        return 2

    if result.status == "failed":
        if result.error:
            print(f"\nFalha: {result.error}")
        if result.r_exit_code not in (None, 0):
            print(f"\nR terminou com codigo {result.r_exit_code}. Ultimas linhas do stderr:")
            print(result.r_stderr_tail)
        if result.verification_issues:
            print("\nVerificacao pos-execucao reprovada:")
            for issue in result.verification_issues:
                print(f"  - {issue}")
        return 3

    print(f"\nSucesso. Saida em: {result.output_path}")

    if result.checkpoint_path:
        print(f"Checkpoint (metricas antes da curadoria assistida): {result.checkpoint_path}")

    if result.llm_mode == "off" and result.llm_skipped_reason:
        print(f"Curadoria assistida por LLM pulada: {result.llm_skipped_reason}")
    elif result.llm_mode in ("live", "mock"):
        print(
            f"Curadoria assistida por LLM ({result.llm_mode}): "
            f"{result.llm_reviewed_count}/{result.llm_total_unique_asvs} ASVs revisadas."
        )
        if result.llm_errors:
            print(f"  {len(result.llm_errors)} erro(s) durante a revisao:")
            for err in result.llm_errors[:5]:
                print(f"    - {err}")

    if result.report_path:
        print(f"Relatorio da execucao: {result.report_path}")
    elif result.report_error:
        print(f"Geracao do relatorio falhou: {result.report_error}")
    elif result.report_mode == "off":
        print("Geracao do relatorio pulada (mesmo motivo da curadoria assistida, ver acima).")

    if result.ecologia_output_dir:
        print(f"Analise ecologica: {result.ecologia_output_dir}")
    elif result.ecologia_error:
        print(f"Analise ecologica falhou: {result.ecologia_error}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
