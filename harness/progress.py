"""Narracao verbosa e obrigatoria do que o harness esta fazendo -- resumo de
entrada antes de rodar qualquer coisa, e o checkpoint entre a curadoria
deterministica e a assistida por LLM.

Nenhuma funcao aqui decide nada sozinha: so formata texto e (no caso de
`default_confirm_llm_stage`) le a resposta do usuario. A logica de quando
cada uma e chamada mora em `harness/orchestrator.py`.
"""

from __future__ import annotations

import sys
from typing import Callable

import pandas as pd

from harness.llm_curation import build_asv_evidence, needs_review

ConfirmFn = Callable[[str], tuple[bool, str]]


def format_input_summary(df: pd.DataFrame, reference_csv: str | None) -> str:
    """Resumo do arquivo de entrada, impresso antes de rodar qualquer coisa
    -- pra quem esta rodando conferir que o arquivo certo foi carregado
    antes de esperar a execucao inteira."""
    researchers = sorted(df["Researcher"].dropna().astype(str).unique()) if "Researcher" in df.columns else []
    projects = sorted(df["Project"].dropna().astype(str).unique()) if "Project" in df.columns else []
    n_rows = len(df)

    n_asvs = df["ASV (Sequence)"].nunique() if "ASV (Sequence)" in df.columns else None
    # Uma ASV pode existir SO num controle (nunca detectada numa amostra
    # real) -- o checkpoint (build_asv_evidence, mais adiante) so considera
    # linhas Type == "Sample", entao o total aqui pode ser maior do que o
    # numero de ASVs que efetivamente entram na curadoria assistida. Mostrar
    # os dois evita que pareca uma contagem inconsistente entre as duas
    # etapas.
    n_asvs_in_samples = (
        df.loc[df["Type"] == "Sample", "ASV (Sequence)"].nunique()
        if "ASV (Sequence)" in df.columns and "Type" in df.columns
        else None
    )

    hit1_cols = ["1_subject header", "1_staxid", "1_indentity", "1_qcovhsp"]
    hit1_present = [c for c in hit1_cols if c in df.columns]
    hit1_filled = (
        int(df[hit1_present].notna().all(axis=1).sum()) if hit1_present else 0
    )

    lines = [
        "=== Resumo da entrada ===",
        f"Researcher: {', '.join(researchers) if researchers else 'nao encontrado'}",
        f"Project: {', '.join(projects) if projects else 'nao encontrado'}",
        f"Linhas (sequencia x amostra): {n_rows}",
    ]
    if n_asvs is not None:
        if n_asvs_in_samples is not None and n_asvs_in_samples != n_asvs:
            lines.append(
                f"Sequencias unicas (ASVs): {n_asvs} no total "
                f"({n_asvs_in_samples} detectada(s) em amostra(s) real(is), "
                f"{n_asvs - n_asvs_in_samples} exclusiva(s) de controle -- "
                "o checkpoint mais adiante conta so as detectadas em amostras reais)"
            )
        else:
            lines.append(f"Sequencias unicas (ASVs): {n_asvs}")
    if hit1_present:
        lines.append(
            f"Hit 1 do BLAST completo (header/staxid/identidade/cobertura): {hit1_filled}/{n_rows} linhas"
        )
    if len(researchers) > 1 or len(projects) > 1:
        lines.append(
            "Aviso: mais de um Researcher/Project neste arquivo -- a curadoria roda sobre "
            "todas as linhas juntas, sem separar resultado por Researcher ou Project."
        )
    if reference_csv:
        lines.append(f"Referencia de especies tradicionais: {reference_csv} (evidencia extra na curadoria assistida)")
    else:
        lines.append("Referencia de especies tradicionais: nenhuma (--reference nao informado)")

    return "\n".join(lines)


def build_checkpoint_stats(df: pd.DataFrame) -> dict:
    """Agrega deterministicamente o que a curadoria em R produziu, pra
    mostrar antes de decidir se vale a pena gastar chamadas de LLM em cima
    disso. Mesmo espirito de `report_generation.build_report_stats`, mas
    sem nada que dependa da curadoria assistida (que ainda nao rodou)."""
    evidence = build_asv_evidence(df)
    review_flags = evidence.apply(needs_review, axis=1) if len(evidence) else pd.Series(dtype=bool)

    return {
        "row_count": len(df),
        "unique_asv_count": len(evidence),
        "needs_review_count": int(review_flags.sum()),
        "identification_counts": df["Identification Max. taxonomy"].value_counts(dropna=False).to_dict(),
        "contamination_counts": df["Contamination status"].value_counts(dropna=False).to_dict(),
        "amplicon_counts": df["Primer expected length"].value_counts(dropna=False).to_dict(),
    }


def format_checkpoint_summary(stats: dict) -> str:
    lines = [
        "=== Checkpoint: curadoria deterministica concluida ===",
        f"Linhas processadas: {stats['row_count']}",
        f"Sequencias unicas (ASVs): {stats['unique_asv_count']}",
        "Identificacao (nivel alcancado, contagem de linhas):",
    ]
    for level, count in stats["identification_counts"].items():
        lines.append(f"  - {level}: {count}")
    lines.append("Contaminacao:")
    for status, count in stats["contamination_counts"].items():
        lines.append(f"  - {status}: {count}")
    lines.append("Faixa de amplicon:")
    for status, count in stats["amplicon_counts"].items():
        lines.append(f"  - {status}: {count}")
    lines.append(
        f"Sequencias que seriam revisadas pela curadoria assistida por LLM: "
        f"{stats['needs_review_count']} de {stats['unique_asv_count']}"
    )
    return "\n".join(lines)


def default_confirm_llm_stage(prompt_text: str) -> tuple[bool, str]:
    """Pergunta ao usuario se quer prosseguir com a curadoria assistida por
    LLM. Sem terminal interativo esperando resposta (avaliacao automatizada,
    saida redirecionada, entrada padrao fechada), prossegue sozinho em vez
    de travar -- a pergunta so bloqueia quando ha alguem de fato pra
    responder."""
    print(prompt_text)

    if not sys.stdin.isatty():
        print("[modo nao interativo detectado -- prosseguindo automaticamente com --llm-mode configurado]")
        return True, "modo nao interativo: prosseguiu automaticamente com o --llm-mode configurado"

    try:
        answer = input("Prosseguir com a curadoria assistida por LLM? [S/n] ").strip().lower()
    except EOFError:
        print("[entrada padrao indisponivel -- prosseguindo automaticamente]")
        return True, "entrada padrao indisponivel: prosseguiu automaticamente"

    if answer in ("", "s", "sim", "y", "yes"):
        return True, "usuario confirmou no checkpoint"
    return False, "usuario optou por pular a curadoria assistida no checkpoint"
