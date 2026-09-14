"""Harness: inicia e acompanha a execucao da curadoria deterministica,
verifica os resultados e registra o que aconteceu (recusa, sucesso ou
falha) para rastreabilidade.

Divisao de responsabilidades (nenhuma logica de curadoria mora aqui):
  - tools/schema_validation.py    valida o contrato de entrada (Python).
  - r/curadoria_deterministica.R  toda a logica determinística (R, chamado
                                   como subprocesso).
  - harness/llm_curation.py       curadoria assistida por LLM (etapa
                                   opcional, so pra ASVs inconclusivas).
  - harness/report_generation.py  relatorio narrativo da execucao (etapa
                                   opcional, apoiada por LLM sobre agregados
                                   ja calculados).
  - harness/orchestrator.py       conecta tudo, verifica a saida e grava
                                   um log estruturado de cada run.

Fluxo: valida -> (recusa se bloqueante) -> roda o R -> verifica a saida ->
curadoria assistida por LLM (opcional) -> relatorio narrativo (opcional) ->
loga. Cada etapa gera um "RunResult" serializavel, gravado em
`runs/<timestamp>.json` (relatorio em `runs/<timestamp>_relatorio.md`),
para responder depois "o que foi feito e por que" sem precisar reexecutar
nada.
"""

from __future__ import annotations

import glob
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from harness.llm_curation import LlmCurationResult, load_traditional_species, run_llm_assisted_curation
from harness.report_generation import ReportContext, ReportGenerationResult, generate_report
from tools.schema_validation import load_column_aliases, load_schema, validate_asv_table

REPO_ROOT = Path(__file__).resolve().parent.parent
R_SCRIPT_PATH = REPO_ROOT / "r" / "curadoria_deterministica.R"
DEFAULT_RUNS_DIR = REPO_ROOT / "runs"

# Colunas que so existem se o pipeline R realmente rodou os 6 blocos ate o
# fim -- uma checagem rapida de sanidade, nao uma revalidacao completa (essa
# ja foi feita pelo schema_validation antes do R rodar).
EXPECTED_OUTPUT_COLUMNS = [
    "BLAST ID",
    "Contamination status",
    "Identification",
    "Identification Max. taxonomy",
    "Possible target taxon",
]


@dataclass
class RunResult:
    status: str  # "refused" | "success" | "failed"
    input_path: str
    output_path: Optional[str]
    started_at: str
    finished_at: str
    validation_summary: str
    validation_blocking: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)
    r_exit_code: Optional[int] = None
    r_stdout_tail: str = ""
    r_stderr_tail: str = ""
    verification_issues: list[str] = field(default_factory=list)
    error: Optional[str] = None
    llm_mode: Optional[str] = None
    llm_reviewed_count: int = 0
    llm_total_unique_asvs: int = 0
    llm_errors: list[str] = field(default_factory=list)
    llm_skipped_reason: Optional[str] = None
    report_path: Optional[str] = None
    report_mode: Optional[str] = None
    report_error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def find_rscript() -> Optional[str]:
    """Localiza o executavel Rscript: primeiro no PATH, depois em locais
    comuns de instalacao no Windows -- o R nem sempre entra no PATH de uma
    sessao de terminal que ja estava aberta antes da instalacao (aconteceu
    nesta propria maquina durante o desenvolvimento)."""
    on_path = shutil.which("Rscript")
    if on_path:
        return on_path

    windows_globs = [
        "C:/Program Files/R/R-*/bin/Rscript.exe",
        "C:/Program Files (x86)/R/R-*/bin/Rscript.exe",
    ]
    for pattern in windows_globs:
        matches = sorted(glob.glob(pattern), reverse=True)
        if matches:
            return matches[0]
    return None


def _tail(text: str, n_lines: int = 30) -> str:
    lines = text.splitlines()
    return "\n".join(lines[-n_lines:])


RScriptRunner = Callable[..., subprocess.CompletedProcess]


def default_r_runner(
    rscript_exe: str,
    input_csv: Path,
    output_csv: Path,
    config_path: Optional[Path],
    timeout: int,
) -> subprocess.CompletedProcess:
    args = [rscript_exe, str(R_SCRIPT_PATH), str(input_csv), f"--output={output_csv}"]
    if config_path is not None:
        args.append(f"--config={config_path}")
    # Nota: em alguns ambientes Windows, mensagens de console do R (cat/
    # message) podem sair com acentos corrompidos aqui (mismatch de
    # encoding entre a codepage nativa do SO e UTF-8) -- isso afeta so texto
    # de diagnostico (r_stdout_tail/r_stderr_tail no log do run), nunca os
    # dados da curadoria em si: o CSV de saida e sempre lido/escrito como
    # UTF-8 (ver verify_output) e nao e afetado.
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout
    )


def verify_output(output_path: Path, expected_row_count: int) -> list[str]:
    """Checagens minimas de sanidade no resultado do R antes de declarar
    sucesso: o arquivo existe, o numero de linhas bate com a entrada (nada
    foi silenciosamente descartado) e as colunas centrais da curadoria
    estao presentes."""
    issues: list[str] = []

    if not output_path.exists():
        issues.append("Arquivo de saida nao foi criado.")
        return issues

    try:
        out_df = pd.read_csv(output_path, sep=";", decimal=",", encoding="utf-8")
    except Exception as exc:  # arquivo corrompido/formato inesperado
        issues.append(f"Nao foi possivel ler o arquivo de saida: {exc}")
        return issues

    if len(out_df) != expected_row_count:
        issues.append(
            f"Numero de linhas mudou entre entrada e saida "
            f"(entrada={expected_row_count}, saida={len(out_df)})."
        )

    missing_cols = [c for c in EXPECTED_OUTPUT_COLUMNS if c not in out_df.columns]
    if missing_cols:
        issues.append(f"Coluna(s) esperada(s) da curadoria ausente(s) na saida: {missing_cols}")

    return issues


def write_run_log(result: RunResult, runs_dir: Path) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = result.started_at.replace(":", "-").replace("+00-00", "Z")
    log_path = runs_dir / f"{timestamp}.json"
    log_path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return log_path


def run(
    input_csv: str | Path,
    config_path: str | Path | None = None,
    output_csv: str | Path | None = None,
    runs_dir: str | Path = DEFAULT_RUNS_DIR,
    r_runner: RScriptRunner = default_r_runner,
    rscript_exe: Optional[str] = None,
    timeout: int = 1800,
    llm_mode: str = "live",
    traditional_species_csv: str | Path | None = None,
) -> RunResult:
    """Executa uma rodada completa: valida -> roda o R -> verifica ->
    curadoria assistida por LLM (opcional) -> loga.

    `config_path`: YAML opcional, repassado tal qual pro R (`--config`). Se
    trouxer a chave `colunas_alias` (nome bruto -> nome canonico), essa mesma
    tradução também é aplicada aqui do lado Python antes de validar -- assim
    uma tabela com nomes de coluna diferentes do canônico (outro projeto,
    mesmo conceito) ainda passa pela checagem de colunas obrigatórias em vez
    de ser recusada por causa do nome.

    `r_runner` e injetavel de proposito -- os testes passam um runner falso
    pra nao depender de R instalado nem de rede (NCBI/GBIF) pra verificar a
    logica de orquestracao em si.

    `llm_mode`: controla tanto a curadoria assistida quanto o relatorio
    narrativo da execucao (harness/report_generation.py) -- "live"
    (default, chama a Groq pras duas etapas -- degrada pra "off" sozinho se
    GROQ_API_KEY nao estiver configurada), "mock" (simula as respostas, sem
    rede -- so testa o encadeamento) ou "off" (pula as duas etapas por
    completo, so entrega a curadoria deterministica).

    `traditional_species_csv`: caminho opcional para a tabela de especies
    obtidas por metodos tradicionais de monitoramento (ver
    `harness.llm_curation.load_traditional_species`), usada so como
    evidencia adicional na curadoria assistida por LLM. Sem ela, essa
    etapa roda normalmente, so sem essa evidencia extra.
    """
    input_csv = Path(input_csv)
    config_path = Path(config_path) if config_path else None
    runs_dir = Path(runs_dir)
    started_at = datetime.now(timezone.utc).isoformat()

    df = pd.read_csv(input_csv, sep=";", decimal=",", encoding="utf-8")
    schema = load_schema()
    column_aliases = load_column_aliases(config_path)
    report = validate_asv_table(df, schema=schema, column_aliases=column_aliases)

    if not report.is_valid:
        result = RunResult(
            status="refused",
            input_path=str(input_csv),
            output_path=None,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            validation_summary=report.summary(),
            validation_blocking=[f"{i.code}: {i.message}" for i in report.blocking],
            validation_warnings=[f"{i.code}: {i.message}" for i in report.warnings],
        )
        write_run_log(result, runs_dir)
        return result

    if output_csv is None:
        output_csv = runs_dir / f"{Path(input_csv).stem}_curado.csv"
    output_csv = Path(output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    resolved_rscript = rscript_exe or find_rscript()
    if resolved_rscript is None:
        result = RunResult(
            status="failed",
            input_path=str(input_csv),
            output_path=str(output_csv),
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            validation_summary=report.summary(),
            validation_warnings=[f"{i.code}: {i.message}" for i in report.warnings],
            error=(
                "Rscript nao encontrado no PATH nem nos locais comuns de instalacao. "
                "Instale R (https://www.r-project.org) ou aponte para o executavel."
            ),
        )
        write_run_log(result, runs_dir)
        return result

    try:
        proc = r_runner(resolved_rscript, input_csv, output_csv, config_path, timeout)
    except subprocess.TimeoutExpired:
        result = RunResult(
            status="failed",
            input_path=str(input_csv),
            output_path=str(output_csv),
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            validation_summary=report.summary(),
            validation_warnings=[f"{i.code}: {i.message}" for i in report.warnings],
            error=f"Execucao do R excedeu o timeout de {timeout}s.",
        )
        write_run_log(result, runs_dir)
        return result

    verification_issues: list[str] = []
    if proc.returncode == 0:
        verification_issues = verify_output(output_csv, expected_row_count=len(df))

    status = "success" if proc.returncode == 0 and not verification_issues else "failed"

    llm_result = None
    curated_df = None
    if status == "success":
        try:
            traditional_species_df = (
                load_traditional_species(traditional_species_csv)
                if traditional_species_csv is not None
                else None
            )
            curated_df = pd.read_csv(output_csv, sep=";", decimal=",", encoding="utf-8")
            curated_df, llm_result = run_llm_assisted_curation(
                curated_df, mode=llm_mode, traditional_species_df=traditional_species_df
            )
            curated_df.to_csv(output_csv, sep=";", decimal=",", index=False, encoding="utf-8")
        except Exception as exc:
            # A curadoria deterministica ja passou na verificacao acima --
            # uma falha inesperada na etapa de LLM (ex. coluna de evidencia
            # ausente por algum motivo imprevisto) nao deve derrubar um
            # resultado ja validado, so ficar registrada.
            llm_result = LlmCurationResult(mode="off", skipped_reason=f"Falha inesperada na curadoria assistida: {exc}")

    report_text = None
    report_result = None
    if status == "success" and curated_df is not None and llm_result is not None:
        try:
            report_context = ReportContext(
                input_path=str(input_csv),
                row_count=len(df),
                started_at=started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                llm_reviewed_count=llm_result.reviewed_count,
                llm_total_unique_asvs=llm_result.total_unique_asvs,
                llm_errors=llm_result.errors,
                llm_skipped_reason=llm_result.skipped_reason,
            )
            report_text, report_result = generate_report(curated_df, report_context, mode=llm_mode)
        except Exception as exc:
            # Mesma logica de protecao acima: uma falha inesperada na geracao
            # do relatorio nao invalida a curadoria ja concluida e verificada.
            report_result = ReportGenerationResult(mode="off", error=f"Falha inesperada na geracao do relatorio: {exc}")

    report_path = None
    if report_text is not None:
        timestamp = started_at.replace(":", "-").replace("+00-00", "Z")
        report_file = runs_dir / f"{timestamp}_relatorio.md"
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(report_text, encoding="utf-8")
        report_path = str(report_file)

    result = RunResult(
        status=status,
        input_path=str(input_csv),
        output_path=str(output_csv) if status == "success" else None,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc).isoformat(),
        validation_summary=report.summary(),
        validation_warnings=[f"{i.code}: {i.message}" for i in report.warnings],
        r_exit_code=proc.returncode,
        r_stdout_tail=_tail(proc.stdout),
        r_stderr_tail=_tail(proc.stderr),
        verification_issues=verification_issues,
        llm_mode=llm_result.mode if llm_result else None,
        llm_reviewed_count=llm_result.reviewed_count if llm_result else 0,
        llm_total_unique_asvs=llm_result.total_unique_asvs if llm_result else 0,
        llm_errors=llm_result.errors if llm_result else [],
        llm_skipped_reason=llm_result.skipped_reason if llm_result else None,
        report_path=report_path,
        report_mode=report_result.mode if report_result else None,
        report_error=report_result.error if report_result else None,
    )
    write_run_log(result, runs_dir)
    return result
