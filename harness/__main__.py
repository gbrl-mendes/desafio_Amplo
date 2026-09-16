"""Ponto de entrada do harness: `python -m harness <entrada.csv> [opcoes]`.

Ve `orchestrator.run` para o fluxo completo (resumo da entrada -> validar ->
rodar R -> verificar -> checkpoint -> curadoria assistida -> analise
ecologica opcional -> logar). Com `--ecologia-somente CURADO.CSV` no lugar
de `<entrada.csv>`, ramifica pra `orchestrator.run_ecologia_somente` --
caminho mais curto que roda so a analise ecologica sobre um CSV ja curado,
sem refazer a curadoria. Este modulo so cuida de argumentos de linha de
comando e do codigo de saida do processo, mesmo mapeamento pros dois
caminhos:
  0 = sucesso, 2 = entrada recusada (validacao, ou CSV/colunas inesperadas
  em --ecologia-somente), 3 = falha de execucao (R ausente, erro no script
  R, verificacao pos-execucao reprovada, etc.).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from harness.orchestrator import DEFAULT_RUNS_DIR, run, run_ecologia_somente


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m harness",
        description="Roda a curadoria deterministica de ASVs (eDNA_Cipo, MiFish2) sobre um CSV de entrada.",
    )
    parser.add_argument(
        "input_csv",
        nargs="?",
        default=None,
        help=(
            "Caminho do CSV de entrada (schema em data/reference/asv_input_schema.yaml). "
            "Obrigatorio, a menos que --ecologia-somente seja usado no lugar."
        ),
    )
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
    parser.add_argument(
        "--groq-api-key",
        dest="groq_api_key",
        default=None,
        help=(
            "Chave da API da Groq para a curadoria assistida e o relatorio narrativo, informada "
            "diretamente na chamada. Tem prioridade sobre GROQ_API_KEY (variavel de ambiente ou "
            ".env). Se preferir nao deixar a chave visivel no historico do terminal, prefira a "
            "variavel de ambiente."
        ),
    )
    parser.add_argument(
        "--groq-model",
        dest="groq_model",
        default=None,
        help="Modelo da Groq a usar, informado diretamente na chamada. Tem prioridade sobre GROQ_MODEL.",
    )
    parser.add_argument(
        "--ecologia-somente",
        dest="ecologia_somente_csv",
        default=None,
        metavar="CURADO.CSV",
        help=(
            "Roda so a analise ecologica sobre um CSV ja curado (opcionalmente editado a mao), "
            "sem refazer a curadoria. Mutuamente exclusivo com <entrada.csv>."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.input_csv is None and args.ecologia_somente_csv is None:
        parser.error("informe <entrada.csv> ou --ecologia-somente CURADO.CSV.")
    if args.input_csv is not None and args.ecologia_somente_csv is not None:
        parser.error("<entrada.csv> e --ecologia-somente sao mutuamente exclusivos -- use so um dos dois.")

    if args.ecologia_somente_csv is not None:
        result = run_ecologia_somente(
            curado_csv_path=args.ecologia_somente_csv,
            config_path=args.config_path,
            reference_path=args.traditional_species_csv,
            runs_dir=args.runs_dir,
        )

        print(f"Status: {result.status}")
        print(result.validation_summary)

        if result.status == "refused":
            print(f"\nEntrada recusada: {result.error or result.validation_summary}")
            return 2
        if result.status == "failed":
            if result.error:
                print(f"\nFalha: {result.error}")
            if result.r_exit_code not in (None, 0):
                print(f"\nR terminou com codigo {result.r_exit_code}. Ultimas linhas do stderr:")
                print(result.r_stderr_tail)
            return 3

        print(f"\nSucesso. Analise ecologica em: {result.ecologia_output_dir}")
        if result.html_report_path:
            print(f"Relatorio HTML: {result.html_report_path}")
        return 0

    result = run(
        input_csv=args.input_csv,
        config_path=args.config_path,
        output_csv=args.output_csv,
        runs_dir=args.runs_dir,
        llm_mode=args.llm_mode,
        traditional_species_csv=args.traditional_species_csv,
        assume_yes=args.assume_yes,
        ecologia=args.ecologia,
        groq_api_key=args.groq_api_key,
        groq_model=args.groq_model,
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

    if result.checkpoint_pre_llm:
        print(
            "Checkpoint pre-LLM: "
            f"{result.checkpoint_pre_llm.get('needs_review_count')} sequencia(s) precisariam de revisao "
            f"({result.checkpoint_pre_llm.get('decision')})."
        )

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
        if result.html_report_path:
            print(f"Relatorio HTML: {result.html_report_path}")
    elif result.ecologia_error:
        print(f"Analise ecologica falhou: {result.ecologia_error}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
