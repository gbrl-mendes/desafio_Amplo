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
  - harness/progress.py           narracao verbose obrigatoria (resumo da
                                   entrada, checkpoint) e a pergunta do
                                   checkpoint em si.
  - harness/orchestrator.py       conecta tudo, verifica a saida e grava
                                   um log estruturado de cada run.

Fluxo: resumo da entrada -> valida -> (recusa se bloqueante) -> roda o R
(saida ao vivo) -> verifica a saida -> checkpoint (metricas + log proprio +
pergunta se ha custo real de LLM em jogo) -> curadoria assistida por LLM
(opcional) -> relatorio narrativo (opcional) -> loga. Cada etapa gera um
"RunResult" serializavel, gravado em `runs/<timestamp>.json` (checkpoint em
`runs/<timestamp>_checkpoint.json`, relatorio em
`runs/<timestamp>_relatorio.md`), para responder depois "o que foi feito e
por que" sem precisar reexecutar nada.
"""

from __future__ import annotations

import glob
import json
import shutil
import subprocess
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
import yaml

from harness.llm_curation import (
    LlmCurationResult,
    add_curated_id_column,
    load_traditional_species,
    run_llm_assisted_curation,
)
from harness.progress import (
    ConfirmFn,
    build_checkpoint_stats,
    default_confirm_llm_stage,
    format_checkpoint_summary,
    format_input_summary,
)
from harness.html_report import build_html_report
from harness.report_generation import ReportContext, ReportGenerationResult, build_report_stats, generate_report
from tools.schema_validation import load_column_aliases, load_schema, validate_asv_table

REPO_ROOT = Path(__file__).resolve().parent.parent
R_SCRIPT_PATH = REPO_ROOT / "r" / "curadoria_deterministica.R"
ECO_SCRIPT_PATH = REPO_ROOT / "r" / "analise_ecologica.R"
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
    checkpoint_path: Optional[str] = None
    ecologia_output_dir: Optional[str] = None
    ecologia_exit_code: Optional[int] = None
    ecologia_error: Optional[str] = None
    tipo_execucao: str = "completo"  # "completo" | "ecologia_somente"
    config_path: Optional[str] = None
    reference_path: Optional[str] = None
    diagnostics_path: Optional[str] = None
    html_report_path: Optional[str] = None

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


def _first_value(df: pd.DataFrame, column: str) -> Optional[str]:
    """Primeiro valor nao nulo de uma coluna, se ela existir -- usado pro
    cabecalho do relatorio HTML (Researcher/Project/Primer), que assume so
    o caso comum de um valor unico por execucao sem validar isso."""
    if column not in df.columns or df[column].dropna().empty:
        return None
    return str(df[column].dropna().iloc[0])


RScriptRunner = Callable[..., subprocess.CompletedProcess]


def _stream_pipe_to_console(pipe, sink: list[str]) -> None:
    """Le uma pipe linha a linha, imprime cada uma na hora (verbose ao vivo
    e obrigatorio, ver harness/progress.py) e acumula pra formar o
    r_stdout_tail/r_stderr_tail do log do run."""
    for line in iter(pipe.readline, ""):
        print(line, end="", flush=True)
        sink.append(line)
    pipe.close()


def run_streaming_subprocess(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Roda um subprocesso transmitindo stdout/stderr ao vivo (linha a linha,
    em threads separadas) em vez de capturar silenciosamente -- e assim que o
    usuario acompanha o progresso de um script R rodando (consulta de
    taxonomia, GBIF, etc.) enquanto a execucao roda, nao so no final. Usado
    tanto pela curadoria deterministica quanto pela analise ecologica.

    Nota: em alguns ambientes Windows, mensagens de console do R (cat/
    message) podem sair com acentos corrompidos aqui (mismatch de encoding
    entre a codepage nativa do SO e UTF-8) -- isso afeta so texto de
    diagnostico (impresso ao vivo e no stdout/stderr do log), nunca os dados
    calculados em si: todo CSV de saida e sempre lido/escrito como UTF-8 e
    nao e afetado.
    """
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    stdout_thread = threading.Thread(target=_stream_pipe_to_console, args=(proc.stdout, stdout_lines))
    stderr_thread = threading.Thread(target=_stream_pipe_to_console, args=(proc.stderr, stderr_lines))
    stdout_thread.start()
    stderr_thread.start()

    try:
        returncode = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise
    finally:
        stdout_thread.join()
        stderr_thread.join()

    return subprocess.CompletedProcess(
        args=args, returncode=returncode, stdout="".join(stdout_lines), stderr="".join(stderr_lines)
    )


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
    return run_streaming_subprocess(args, timeout)


EcoRunner = Callable[..., subprocess.CompletedProcess]


def default_eco_runner(
    rscript_exe: str,
    curated_csv: Path,
    output_dir: Path,
    config_path: Optional[Path],
    traditional_species_csv: Optional[Path],
    timeout: int,
) -> subprocess.CompletedProcess:
    args = [rscript_exe, str(ECO_SCRIPT_PATH), str(curated_csv), f"--output-dir={output_dir}"]
    if config_path is not None:
        args.append(f"--config={config_path}")
    if traditional_species_csv is not None:
        args.append(f"--reference={traditional_species_csv}")
    return run_streaming_subprocess(args, timeout)


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
    confirm_llm_stage: ConfirmFn = default_confirm_llm_stage,
    assume_yes: bool = False,
    ecologia: bool = False,
    eco_runner: EcoRunner = default_eco_runner,
    groq_api_key: Optional[str] = None,
    groq_model: Optional[str] = None,
) -> RunResult:
    """Executa uma rodada completa: resumo da entrada -> valida -> roda o R
    (saida ao vivo) -> verifica -> checkpoint -> curadoria assistida por LLM
    (opcional) -> analise ecologica (opcional) -> loga.

    `confirm_llm_stage`: injetavel de proposito, mesmo padrao de `r_runner`
    -- os testes passam uma funcao falsa em vez de depender de stdin. O
    default (`harness.progress.default_confirm_llm_stage`) pergunta ao
    usuario num terminal interativo, ou prossegue sozinho (registrando o
    motivo) se nao houver terminal esperando resposta -- nunca trava uma
    execucao automatizada.

    `assume_yes`: pula a pergunta do checkpoint e ja prossegue com o
    `llm_mode` configurado, sem chamar `confirm_llm_stage`. Pra quem ja
    sabe que quer rodar sem parar (ex. reexecucoes durante desenvolvimento).

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
    `harness.llm_curation.load_traditional_species`), usada tanto como
    evidencia adicional na curadoria assistida por LLM quanto (se
    `ecologia=True`) na comparacao eDNA x metodos tradicionais.

    `ecologia`: quando True, roda `r/analise_ecologica.R` sobre o CSV final
    (com `Curated ID` ja preenchida) logo apos a curadoria assistida, escrevendo
    tabelas e graficos em `runs/<timestamp>_ecologia/`. Uma falha aqui nunca
    invalida a curadoria ja concluida e verificada, so fica registrada em
    `ecologia_error`. `eco_runner` e injetavel de proposito, mesmo padrao de
    `r_runner`.

    `groq_api_key`/`groq_model`: repassados tal qual pra `run_llm_assisted_curation`
    e `generate_report`, que ja resolvem a prioridade internamente (parametro
    > variavel de ambiente > `.env` > etapa pulada). Util pra quem prefere
    informar a chave direto na chamada em vez de configurar `.env`.
    """
    input_csv = Path(input_csv)
    config_path = Path(config_path) if config_path else None
    runs_dir = Path(runs_dir)
    started_at = datetime.now(timezone.utc).isoformat()

    df = pd.read_csv(input_csv, sep=";", decimal=",", encoding="utf-8")
    print(format_input_summary(df, str(traditional_species_csv) if traditional_species_csv else None))

    schema = load_schema()
    column_aliases = load_column_aliases(config_path)
    report = validate_asv_table(df, schema=schema, column_aliases=column_aliases)
    print(f"\n{report.summary()}")

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
        run_date = started_at[:10]  # started_at e um ISO datetime, "YYYY-MM-DD..."
        output_csv = runs_dir / f"output_pos_curadoria_LLM-{run_date}.csv"
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

    print("\n=== Rodando curadoria deterministica (R) ===")
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

    # Mesma convencao de nome usada por curadoria_deterministica.qmd::main()
    # pra gravar o diagnostics.json (ver C1) -- ao lado do CSV de saida,
    # trocando so a extensao. So existe de fato se o R realmente escreveu.
    diagnostics_candidate = Path(str(output_csv).rsplit(".csv", 1)[0] + "_diagnostics.json")
    diagnostics_path = str(diagnostics_candidate) if status == "success" and diagnostics_candidate.exists() else None

    llm_result = None
    curated_df = None
    deterministic_df = None
    checkpoint_path = None
    effective_llm_mode = llm_mode
    if status == "success":
        try:
            traditional_species_df = (
                load_traditional_species(traditional_species_csv)
                if traditional_species_csv is not None
                else None
            )
            curated_df = pd.read_csv(output_csv, sep=";", decimal=",", encoding="utf-8")
            # Guardado antes da etapa de LLM sobrescrever curated_df -- e o
            # que o relatorio HTML (secao 5, ver harness/html_report.py)
            # mostra como "saida do determinístico antes da LLM".
            deterministic_df = curated_df.copy()

            # Checkpoint: mostra o que a curadoria deterministica encontrou,
            # grava um log proprio desse trecho, e (so quando --llm-mode=live
            # e ha custo/tempo real de API em jogo) pergunta se vale a pena
            # seguir pra curadoria assistida.
            checkpoint_stats = build_checkpoint_stats(curated_df)
            print("\n" + format_checkpoint_summary(checkpoint_stats))

            checkpoint_decision = "llm-mode != live: checkpoint nao pergunta, so registra"
            if llm_mode == "live":
                if assume_yes:
                    checkpoint_decision = "assume_yes: prosseguiu sem perguntar"
                else:
                    prompt_text = (
                        f"\n{checkpoint_stats['needs_review_count']} de "
                        f"{checkpoint_stats['unique_asv_count']} sequencia(s) unica(s) vao ser "
                        "enviadas pra Groq na curadoria assistida."
                    )
                    proceed, checkpoint_decision = confirm_llm_stage(prompt_text)
                    if not proceed:
                        effective_llm_mode = "off"

            checkpoint_timestamp = started_at.replace(":", "-").replace("+00-00", "Z")
            checkpoint_file = runs_dir / f"{checkpoint_timestamp}_checkpoint.json"
            checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_file.write_text(
                json.dumps(
                    {
                        **checkpoint_stats,
                        "llm_mode_requested": llm_mode,
                        "llm_mode_effective": effective_llm_mode,
                        "decision": checkpoint_decision,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            checkpoint_path = str(checkpoint_file)

            if effective_llm_mode != llm_mode:
                print(f"\n=== Curadoria assistida por LLM pulada no checkpoint ({checkpoint_decision}) ===")
            else:
                print("\n=== Curadoria assistida por LLM ===")

            curated_df, llm_result = run_llm_assisted_curation(
                curated_df,
                mode=effective_llm_mode,
                traditional_species_df=traditional_species_df,
                api_key=groq_api_key,
                model=groq_model,
            )
            curated_df = add_curated_id_column(curated_df)
            curated_df.to_csv(output_csv, sep=";", decimal=",", index=False, encoding="utf-8")
        except Exception as exc:
            # A curadoria deterministica ja passou na verificacao acima --
            # uma falha inesperada na etapa de LLM (ex. coluna de evidencia
            # ausente por algum motivo imprevisto) nao deve derrubar um
            # resultado ja validado, so ficar registrada.
            llm_result = LlmCurationResult(mode="off", skipped_reason=f"Falha inesperada na curadoria assistida: {exc}")

    report_text = None
    report_result = None
    report_stats = None
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
            # Agregados puros (sem LLM) que o relatorio HTML (secao 6, ver
            # harness/html_report.py) tambem usa pros casos de divergencia --
            # calculado aqui em vez de so dentro de generate_report() porque
            # esse ultimo pula o calculo inteiro quando llm_mode == "off".
            report_stats = build_report_stats(curated_df, report_context)
            print("\n=== Gerando relatorio narrativo ===")
            report_text, report_result = generate_report(
                curated_df, report_context, mode=effective_llm_mode, api_key=groq_api_key, model=groq_model
            )
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

    ecologia_output_dir = None
    ecologia_exit_code = None
    ecologia_error = None
    if ecologia and status == "success" and curated_df is not None:
        eco_resolved_rscript = rscript_exe or find_rscript()
        if eco_resolved_rscript is None:
            ecologia_error = "Rscript nao encontrado -- analise ecologica pulada."
        else:
            try:
                eco_timestamp = started_at.replace(":", "-").replace("+00-00", "Z")
                eco_dir = runs_dir / f"{eco_timestamp}_ecologia"
                print("\n=== Analise ecologica ===")
                eco_proc = eco_runner(
                    eco_resolved_rscript,
                    output_csv,
                    eco_dir,
                    config_path,
                    Path(traditional_species_csv) if traditional_species_csv else None,
                    timeout,
                )
                ecologia_exit_code = eco_proc.returncode
                if eco_proc.returncode == 0:
                    ecologia_output_dir = str(eco_dir)
                else:
                    ecologia_error = f"analise_ecologica.R terminou com codigo {eco_proc.returncode}."
            except Exception as exc:
                # Mesma logica de protecao das etapas acima: uma falha aqui
                # nao invalida a curadoria (e o LLM) ja concluidos e verificados.
                ecologia_error = f"Falha inesperada na analise ecologica: {exc}"

    html_report_path = None
    if ecologia_output_dir is not None:
        try:
            html_context = {
                "tipo_execucao": "completo",
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "researcher": _first_value(df, "Researcher"),
                "project": _first_value(df, "Project"),
                "primer": _first_value(df, "Primer"),
                "input_df": df,
                "diagnostics": json.loads(Path(diagnostics_path).read_text(encoding="utf-8")) if diagnostics_path else None,
                "deterministic_df": deterministic_df,
                "report_text": report_text,
                "llm_divergence_examples": report_stats.get("llm_divergence_examples") if report_stats else [],
                "llm_reviewed_count": llm_result.reviewed_count if llm_result else None,
                "llm_total_unique_asvs": llm_result.total_unique_asvs if llm_result else None,
                "final_df": curated_df,
                "ecologia_output_dir": ecologia_output_dir,
                "ecologia_requested": True,
            }
            html_text = build_html_report(html_context)
            html_timestamp = started_at.replace(":", "-").replace("+00-00", "Z")
            html_file = runs_dir / f"{html_timestamp}_report.html"
            html_file.write_text(html_text, encoding="utf-8")
            html_report_path = str(html_file)
        except Exception:
            # Mesma logica de protecao das etapas acima: uma falha aqui nao
            # invalida a curadoria (e a analise ecologica) ja concluidas.
            html_report_path = None

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
        checkpoint_path=checkpoint_path,
        ecologia_output_dir=ecologia_output_dir,
        ecologia_exit_code=ecologia_exit_code,
        ecologia_error=ecologia_error,
        config_path=str(config_path) if config_path else None,
        reference_path=str(traditional_species_csv) if traditional_species_csv else None,
        diagnostics_path=diagnostics_path,
        html_report_path=html_report_path,
    )
    write_run_log(result, runs_dir)
    return result


# Nomes de coluna que run_ecologia_somente() confere no cabecalho do CSV.
# Mantido sincronizado manualmente com DEFAULT_ECO_CONFIG$ecologia em
# r/analise_ecologica.qmd -- se um dia divergirem, este e la sao os dois
# lugares a atualizar.
DEFAULT_ECO_CONFIG_COLUMNS = {
    "coluna_grupo": "Ponto",
    "coluna_id": "Curated ID",
    "coluna_abundancia": "ASV absolute abundance",
}


def _load_eco_config_columns(config_path: Optional[Path]) -> dict[str, str]:
    resolved = dict(DEFAULT_ECO_CONFIG_COLUMNS)
    if config_path is None:
        return resolved
    with open(config_path, encoding="utf-8") as fh:
        user_config = yaml.safe_load(fh) or {}
    eco_section = user_config.get("ecologia") or {}
    resolved.update({k: v for k, v in eco_section.items() if k in resolved})
    return resolved


def _read_csv_header(path: Path) -> list[str]:
    """Le so a primeira linha do CSV (nunca a tabela inteira) e devolve os
    nomes de coluna, splitando por `;` -- suficiente pra checar as colunas
    minimas de `run_ecologia_somente` sem o custo de um `pd.read_csv`
    completo num arquivo que pode ser grande."""
    with open(path, encoding="utf-8") as fh:
        first_line = fh.readline()
    return [c.strip() for c in first_line.rstrip("\r\n").split(";")]


def run_ecologia_somente(
    curado_csv_path: str | Path,
    config_path: str | Path | None = None,
    reference_path: str | Path | None = None,
    runs_dir: str | Path = DEFAULT_RUNS_DIR,
    rscript_exe: Optional[str] = None,
    timeout: int = 1800,
    eco_runner: EcoRunner = default_eco_runner,
) -> RunResult:
    """Roda so a analise ecologica sobre um CSV ja curado por uma execucao
    anterior (opcionalmente revisado a mao), sem refazer nenhuma etapa de
    curadoria -- caminho separado de `run()`, mais curto: confere so o
    cabecalho do CSV (nunca a tabela inteira) e chama `r/analise_ecologica.R`
    como subprocesso, reaproveitando o mesmo `eco_runner` que a flag
    `--ecologia` de `run()` ja usa internamente.

    Grava `runs/<timestamp>.json` no mesmo formato dos outros logs, com
    `tipo_execucao="ecologia_somente"`.
    """
    curado_csv_path = Path(curado_csv_path)
    config_path = Path(config_path) if config_path else None
    reference_path = Path(reference_path) if reference_path else None
    runs_dir = Path(runs_dir)
    started_at = datetime.now(timezone.utc).isoformat()

    def _refused(reason: str) -> RunResult:
        result = RunResult(
            status="refused",
            input_path=str(curado_csv_path),
            output_path=None,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            validation_summary=reason,
            tipo_execucao="ecologia_somente",
            config_path=str(config_path) if config_path else None,
            reference_path=str(reference_path) if reference_path else None,
        )
        write_run_log(result, runs_dir)
        return result

    def _failed(error: str, **extra) -> RunResult:
        result = RunResult(
            status="failed",
            input_path=str(curado_csv_path),
            output_path=None,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            validation_summary="Cabecalho do CSV conferido, colunas minimas presentes.",
            error=error,
            tipo_execucao="ecologia_somente",
            config_path=str(config_path) if config_path else None,
            reference_path=str(reference_path) if reference_path else None,
            **extra,
        )
        write_run_log(result, runs_dir)
        return result

    eco_columns = _load_eco_config_columns(config_path)
    required_columns = [
        eco_columns["coluna_grupo"],
        eco_columns["coluna_id"],
        eco_columns["coluna_abundancia"],
        "Sample",
        "Type",
    ]

    try:
        header = _read_csv_header(curado_csv_path)
    except UnicodeDecodeError as exc:
        return _refused(
            f"Nao foi possivel ler o cabecalho de '{curado_csv_path}' como UTF-8 ({exc}). "
            "Causa provavel: o arquivo foi editado num programa que trocou o encoding sem "
            "perceber -- o formato esperado e sempre UTF-8."
        )
    except OSError as exc:
        return _refused(f"Nao foi possivel ler o arquivo '{curado_csv_path}': {exc}.")

    missing = [c for c in required_columns if c not in header]
    if len(header) <= 1 or missing:
        detalhe = (
            "o cabecalho nao tem nenhum ';' (uma unica coluna encontrada)"
            if len(header) <= 1
            else f"coluna(s) ausente(s): {missing}"
        )
        return _refused(
            f"CSV nao tem o formato esperado pela analise ecologica ({detalhe}). Causa "
            "provavel: o arquivo foi editado num programa que trocou o delimitador "
            "(esperado ';') ou removeu/renomeou uma coluna sem perceber."
        )

    resolved_rscript = rscript_exe or find_rscript()
    if resolved_rscript is None:
        return _failed(
            "Rscript nao encontrado no PATH nem nos locais comuns de instalacao. "
            "Instale R (https://www.r-project.org) ou aponte para o executavel."
        )

    eco_timestamp = started_at.replace(":", "-").replace("+00-00", "Z")
    eco_dir = runs_dir / f"{eco_timestamp}_ecologia"
    print("\n=== Analise ecologica (--ecologia-somente) ===")
    try:
        eco_proc = eco_runner(resolved_rscript, curado_csv_path, eco_dir, config_path, reference_path, timeout)
    except subprocess.TimeoutExpired:
        return _failed(f"Execucao do R excedeu o timeout de {timeout}s.")

    status = "success" if eco_proc.returncode == 0 else "failed"
    eco_error = None if status == "success" else f"analise_ecologica.R terminou com codigo {eco_proc.returncode}."

    html_report_path = None
    if status == "success":
        try:
            curated_df = pd.read_csv(curado_csv_path, sep=";", decimal=",", encoding="utf-8")
            html_context = {
                "tipo_execucao": "ecologia_somente",
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "researcher": _first_value(curated_df, "Researcher"),
                "project": _first_value(curated_df, "Project"),
                "primer": _first_value(curated_df, "Primer"),
                "final_df": curated_df,
                "ecologia_output_dir": str(eco_dir),
                "ecologia_requested": True,
            }
            html_text = build_html_report(html_context)
            html_file = runs_dir / f"{eco_timestamp}_report.html"
            html_file.write_text(html_text, encoding="utf-8")
            html_report_path = str(html_file)
        except Exception:
            # Mesma logica de protecao do resto do modulo: uma falha aqui
            # nao invalida a analise ecologica ja concluida.
            html_report_path = None

    result = RunResult(
        status=status,
        input_path=str(curado_csv_path),
        output_path=str(eco_dir) if status == "success" else None,
        started_at=started_at,
        finished_at=datetime.now(timezone.utc).isoformat(),
        validation_summary="Cabecalho do CSV conferido, colunas minimas presentes.",
        r_exit_code=eco_proc.returncode,
        r_stdout_tail=_tail(eco_proc.stdout),
        r_stderr_tail=_tail(eco_proc.stderr),
        error=eco_error,
        tipo_execucao="ecologia_somente",
        config_path=str(config_path) if config_path else None,
        reference_path=str(reference_path) if reference_path else None,
        ecologia_output_dir=str(eco_dir) if status == "success" else None,
        ecologia_exit_code=eco_proc.returncode,
        ecologia_error=eco_error,
        html_report_path=html_report_path,
    )
    write_run_log(result, runs_dir)
    return result
