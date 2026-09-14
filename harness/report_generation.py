"""Geracao de relatorio narrativo pos-curadoria -- etapa opcional, apoiada
por LLM, que sintetiza os resultados de uma execucao (curadoria
deterministica + assistida) num relatorio tecnico em markdown, gravado ao
lado do log JSON de cada run (`runs/<timestamp>_relatorio.md`).

Mesma disciplina de evidencia da curadoria assistida (llm_curation.py): o
LLM nunca le a tabela bruta nem faz conta nenhuma -- so recebe agregados ja
calculados deterministicamente em Python (`build_report_stats`) e formata
prosa em cima deles, com instrucao explicita de tom (registro tecnico
direto, sem meta-comentario sobre ser gerado por IA).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import pandas as pd
import requests

from harness.llm_curation import DEFAULT_MODEL, GROQ_BASE_URL, extract_groq_error_message, load_env

REPORT_PROMPT_TEMPLATE = """Você escreve relatórios técnicos de curadoria de dados de eDNA \
(metabarcoding) de peixes para uma consultoria ambiental. Abaixo estão os números e casos \
concretos de UMA execução real do pipeline de curadoria (determinística + assistida por LLM), \
já calculados -- você NÃO tem acesso aos dados brutos e não deve inventar nenhum número, caso \
ou observação além do que está listado.

DADOS DA EXECUÇÃO:
{stats_block}

TAREFA: Escreva, em português, um relatório técnico em markdown descrevendo essa execução, \
para uma cientista de dados que vai usar esse resultado num relatório ambiental. Estrutura \
esperada:
1. Contexto da execução (entrada, tamanho, janela de tempo).
2. Resumo quantitativo (identificação, contaminação, faixa de amplicon, curadoria assistida).
3. Casos concretos em que a curadoria assistida divergiu do resultado determinístico, citando \
a justificativa de cada um (use exatamente os casos listados acima, não invente outros; se não \
houver nenhum listado, diga que nenhuma divergência ocorreu).
4. Limitações da execução, se houver erros ou etapas puladas listadas acima.

REGRAS DE TOM: escreva em registro técnico direto, como um relatório real. Não use frases como \
"isso não é hipotético", "nenhum número foi inventado", "o sistema não falhou" ou qualquer \
comentário sobre a transparência/confiabilidade do próprio texto -- apenas apresente os fatos. \
Não inclua nenhum número, caso ou observação que não esteja nos dados acima. Responda só com o \
markdown do relatório, sem texto fora dele.
"""


@dataclass
class ReportContext:
    """Dados de uma execucao ja concluida, necessarios pra montar o
    relatorio -- evita que report_generation dependa de orchestrator.RunResult
    (que por sua vez ja depende deste modulo)."""

    input_path: str
    row_count: int
    started_at: str
    finished_at: str
    llm_reviewed_count: int
    llm_total_unique_asvs: int
    llm_errors: list[str] = field(default_factory=list)
    llm_skipped_reason: Optional[str] = None


@dataclass
class ReportGenerationResult:
    mode: str  # "live" | "mock" | "off"
    error: Optional[str] = None
    skipped_reason: Optional[str] = None


def build_report_stats(df: pd.DataFrame, context: ReportContext) -> dict:
    """Agrega deterministicamente (Python puro, sem LLM) tudo que o
    relatorio precisa citar: contagens por coluna determinística e os casos
    em que a curadoria assistida divergiu da identificação determinística
    (uma linha por ASV única, não por amostra)."""
    per_asv = df.drop_duplicates(subset=["ASV header"])

    diverging = pd.DataFrame()
    if "Assisted ID (LLM)" in per_asv.columns:
        reviewed = per_asv[per_asv["Assisted ID (LLM)"].notna()]
        diverging = reviewed[reviewed["Assisted ID (LLM)"] != reviewed["Identification"]]

    divergence_examples = diverging.sort_values("ASV header").head(5)[
        [
            "ASV header",
            "BLAST ID",
            "Identification",
            "Assisted ID (LLM)",
            "Assisted Confidence (LLM)",
            "Assisted Justification (LLM)",
        ]
    ].to_dict(orient="records") if len(diverging) else []

    return {
        "input_path": context.input_path,
        "row_count": context.row_count,
        "started_at": context.started_at,
        "finished_at": context.finished_at,
        "identification_counts": df["Identification Max. taxonomy"].value_counts(dropna=False).to_dict(),
        "contamination_counts": df["Contamination status"].value_counts(dropna=False).to_dict(),
        "amplicon_counts": df["Primer expected length"].value_counts(dropna=False).to_dict(),
        "llm_total_unique_asvs": context.llm_total_unique_asvs,
        "llm_reviewed_count": context.llm_reviewed_count,
        "llm_divergence_count": len(diverging),
        "llm_divergence_examples": divergence_examples,
        "llm_errors": context.llm_errors,
        "llm_skipped_reason": context.llm_skipped_reason,
    }


def _format_stats_block(stats: dict) -> str:
    lines = [
        f"- Entrada: {stats['input_path']} ({stats['row_count']} linhas)",
        f"- Janela de execução: {stats['started_at']} a {stats['finished_at']}",
        "- Identificação determinística (nível alcançado, contagem de linhas):",
    ]
    for level, count in stats["identification_counts"].items():
        lines.append(f"    - {level}: {count}")

    lines.append("- Status de contaminação (contagem de linhas):")
    for status, count in stats["contamination_counts"].items():
        lines.append(f"    - {status}: {count}")

    lines.append("- Faixa de amplicon esperada pro primer (contagem de linhas):")
    for status, count in stats["amplicon_counts"].items():
        lines.append(f"    - {status}: {count}")

    lines.append(
        f"- Curadoria assistida por LLM: {stats['llm_reviewed_count']}/{stats['llm_total_unique_asvs']} "
        f"ASVs únicas revisadas; {stats['llm_divergence_count']} receberam identificação diferente "
        "da determinística."
    )

    if stats["llm_divergence_examples"]:
        lines.append("- Casos de divergência (use só estes, não invente outros):")
        for ex in stats["llm_divergence_examples"]:
            lines.append(
                f"    - ASV {ex['ASV header']}: BLAST ID={ex['BLAST ID']}, "
                f"Identification (determinística)={ex['Identification']}, "
                f"Assisted ID (LLM)={ex['Assisted ID (LLM)']} "
                f"(confiança {ex['Assisted Confidence (LLM)']}): {ex['Assisted Justification (LLM)']}"
            )
    else:
        lines.append("- Casos de divergência: nenhum.")

    if stats["llm_errors"]:
        lines.append(f"- Erros durante a curadoria assistida ({len(stats['llm_errors'])}):")
        for err in stats["llm_errors"][:5]:
            lines.append(f"    - {err}")

    if stats["llm_skipped_reason"]:
        lines.append(f"- Curadoria assistida pulada nesta execução: {stats['llm_skipped_reason']}")

    return "\n".join(lines)


def build_report_prompt(stats: dict) -> str:
    return REPORT_PROMPT_TEMPLATE.format(stats_block=_format_stats_block(stats))


def _parse_report_text(content: str) -> str:
    return content.strip()


def call_groq_report(
    prompt: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    timeout: int = 90,
    max_retries: int = 4,
) -> str:
    """Mesmo esquema de retry/backoff de `llm_curation.call_groq`, mas sem
    forcar JSON -- aqui a resposta esperada e o markdown do relatorio."""
    last_error: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            resp = requests.post(
                f"{GROQ_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
                timeout=timeout,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(extract_groq_error_message(resp), response=resp)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return _parse_report_text(content)
        except (requests.RequestException, KeyError, IndexError) as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(2**attempt)

    raise RuntimeError(f"Falha ao consultar a Groq apos {max_retries} tentativas: {last_error}")


def mock_report(stats: dict) -> str:
    """Resposta simulada, sem rede -- so pra testar o encadeamento sem
    gastar cota de API real."""
    return (
        f"# [mock] Relatório simulado\n\n"
        f"Execução sobre {stats['row_count']} linhas, sem chamada real ao LLM."
    )


ReportCaller = Callable[[str, str, str], str]


def generate_report(
    df: pd.DataFrame,
    context: ReportContext,
    mode: str = "live",
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    caller: Optional[ReportCaller] = None,
    verbose: bool = True,
) -> tuple[Optional[str], ReportGenerationResult]:
    """Gera o relatorio narrativo (markdown) dessa execucao.

    `caller` e injetavel de proposito (mesmo padrao de `llm_curation.py`)
    -- os testes passam um caller falso pra nao depender de rede nem de
    chave real. Retorna (texto_do_relatorio, resultado); texto e None se a
    etapa foi pulada ou falhou.
    """
    if mode == "off":
        return None, ReportGenerationResult(mode="off", skipped_reason="Modo 'off' explicitamente selecionado.")

    load_env()
    import os

    resolved_key = api_key or os.environ.get("GROQ_API_KEY")
    resolved_model = model or os.environ.get("GROQ_MODEL", DEFAULT_MODEL)

    if mode == "live" and not resolved_key:
        if verbose:
            print(
                "Geracao de relatorio pulada -- GROQ_API_KEY nao configurada "
                "(defina no .env ou como variavel de ambiente)."
            )
        return None, ReportGenerationResult(mode="off", skipped_reason="GROQ_API_KEY nao configurada.")

    stats = build_report_stats(df, context)
    prompt = build_report_prompt(stats)

    if caller is None:
        caller = call_groq_report

    try:
        report_text = mock_report(stats) if mode == "mock" else caller(prompt, resolved_key, resolved_model)
        if verbose:
            print(f"[{mode}] Relatorio de execucao gerado ({len(report_text)} caracteres).")
        return report_text, ReportGenerationResult(mode=mode)
    except Exception as exc:  # falha na geracao do relatorio nao derruba o resto do run
        return None, ReportGenerationResult(mode=mode, error=str(exc))
