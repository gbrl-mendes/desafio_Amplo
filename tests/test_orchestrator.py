"""Testes de harness.orchestrator.

Usa um "R runner" falso (injetado via parametro `r_runner`) em vez de
chamar Rscript de verdade -- estes testes nunca tocam rede (NCBI/GBIF) nem
dependem de R instalado, so verificam a logica de orquestracao (validar ->
rodar -> verificar -> logar).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pandas as pd

from harness.orchestrator import run
from tools.schema_validation import load_schema

SCHEMA = load_schema()
REQUIRED_COLS = [c["name"] for c in SCHEMA["columns"] if c["role"] == "input" and c.get("required")]

OUTPUT_CURATION_COLUMNS = [
    "BLAST ID",
    "Contamination status",
    "Identification",
    "Identification Max. taxonomy",
    "Possible target taxon",
]


def _valid_input_df(n_rows: int = 3) -> pd.DataFrame:
    data = {}
    for col in REQUIRED_COLS:
        if col == "ASV (Sequence)":
            data[col] = [f"ACGT{j}" for j in range(n_rows)]
        elif col == "Unique_File_name":
            data[col] = [f"SC{j}A" for j in range(n_rows)]
        elif "indentity" in col or "qcovhsp" in col:
            data[col] = [95.0] * n_rows
        elif "staxid" in col:
            data[col] = [9606] * n_rows
        elif col in ("Sample total abundance", "ASV absolute abundance"):
            data[col] = [100] * n_rows
        else:
            data[col] = [f"value_{j}" for j in range(n_rows)]
    return pd.DataFrame(data)


def _write_input_csv(path: Path, df: pd.DataFrame) -> None:
    df.to_csv(path, sep=";", decimal=",", index=False, encoding="utf-8")


def _add_curation_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in OUTPUT_CURATION_COLUMNS:
        df[col] = "x"
    return df


def _fake_success_runner(rscript_exe, input_csv, output_csv, config_path, timeout):
    df = pd.read_csv(input_csv, sep=";", decimal=",", encoding="utf-8")
    _add_curation_columns(df).to_csv(output_csv, sep=";", decimal=",", index=False, encoding="utf-8")
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")


def test_refused_when_missing_required_columns(tmp_path):
    df = _valid_input_df().drop(columns=["1_subject header"])
    input_csv = tmp_path / "entrada.csv"
    _write_input_csv(input_csv, df)

    result = run(input_csv, runs_dir=tmp_path / "runs")

    assert result.status == "refused"
    assert any("missing_required_columns" in b for b in result.validation_blocking)
    assert result.output_path is None
    assert list((tmp_path / "runs").glob("*.json"))


def test_success_when_valid_input_and_r_succeeds(tmp_path):
    df = _valid_input_df()
    input_csv = tmp_path / "entrada.csv"
    _write_input_csv(input_csv, df)
    output_csv = tmp_path / "saida.csv"

    result = run(
        input_csv,
        output_csv=output_csv,
        runs_dir=tmp_path / "runs",
        r_runner=_fake_success_runner,
        rscript_exe="rscript-fake",
        llm_mode="off",  # etapa de LLM tem seus proprios testes em test_llm_curation.py
    )

    assert result.status == "success"
    assert result.output_path == str(output_csv)
    assert output_csv.exists()


def test_refused_when_required_column_renamed_without_alias_config(tmp_path):
    # Sanity check for the next test: without colunas_alias, a renamed
    # required column really does get refused (proves the alias mechanism
    # is doing something, not that validation was already lenient).
    df = _valid_input_df().rename(columns={"Researcher": "Pesquisador"})
    input_csv = tmp_path / "entrada.csv"
    _write_input_csv(input_csv, df)

    result = run(input_csv, runs_dir=tmp_path / "runs")

    assert result.status == "refused"
    assert any("missing_required_columns" in b for b in result.validation_blocking)


def test_column_aliases_from_config_let_renamed_input_pass(tmp_path):
    df = _valid_input_df().rename(columns={"Researcher": "Pesquisador"})
    input_csv = tmp_path / "entrada.csv"
    _write_input_csv(input_csv, df)
    output_csv = tmp_path / "saida.csv"
    config_path = tmp_path / "config.yaml"
    config_path.write_text("colunas_alias:\n  Pesquisador: Researcher\n", encoding="utf-8")

    result = run(
        input_csv,
        config_path=config_path,
        output_csv=output_csv,
        runs_dir=tmp_path / "runs",
        r_runner=_fake_success_runner,
        rscript_exe="rscript-fake",
        llm_mode="off",
    )

    assert result.status == "success", result.validation_summary


def _fake_success_runner_with_evidence(rscript_exe, input_csv, output_csv, config_path, timeout):
    """Como `_fake_success_runner`, mas com as colunas de evidencia que
    harness.llm_curation e harness.report_generation esperam -- necessarias
    so quando o teste exercita llm_mode != "off"."""
    df = pd.read_csv(input_csv, sep=";", decimal=",", encoding="utf-8")
    df = _add_curation_columns(df)
    n = len(df)
    df["Type"] = "Sample"
    df["ASV header"] = [f">ASV_{i}-150bp" for i in range(n)]
    df["Identification Max. taxonomy"] = "Species"
    df["Primer expected length"] = "in range"
    df["Selected_Hit_Origin"] = "1"
    df["Genus (NCBI)"] = "Astyanax"
    df["Family (NCBI)"] = "Characidae"
    df["Order (NCBI)"] = "Characiformes"
    df["Class (NCBI)"] = "Actinopteri"
    df["BLASTn pseudo-score"] = 99.0
    df["Vizinhos filogeneticos (k)"] = ""
    df["GBIF regional occurrence count"] = 10
    df["Ponto"] = "SC1"
    df["Habitat"] = "Cânion"
    df["Rios"] = "Mascate"
    df.to_csv(output_csv, sep=";", decimal=",", index=False, encoding="utf-8")
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")


def test_success_writes_report_alongside_json_log_in_mock_mode(tmp_path):
    df = _valid_input_df()
    input_csv = tmp_path / "entrada.csv"
    _write_input_csv(input_csv, df)
    output_csv = tmp_path / "saida.csv"
    runs_dir = tmp_path / "runs"

    result = run(
        input_csv,
        output_csv=output_csv,
        runs_dir=runs_dir,
        r_runner=_fake_success_runner_with_evidence,
        rscript_exe="rscript-fake",
        llm_mode="mock",  # mesma flag liga curadoria assistida e relatorio, sem rede
    )

    assert result.status == "success"
    assert result.report_mode == "mock"
    assert result.report_path is not None
    report_file = Path(result.report_path)
    assert report_file.exists()
    assert report_file.parent == runs_dir
    assert "[mock]" in report_file.read_text(encoding="utf-8")


def test_checkpoint_writes_own_log_regardless_of_llm_mode(tmp_path):
    df = _valid_input_df()
    input_csv = tmp_path / "entrada.csv"
    _write_input_csv(input_csv, df)
    runs_dir = tmp_path / "runs"

    result = run(
        input_csv,
        runs_dir=runs_dir,
        r_runner=_fake_success_runner_with_evidence,
        rscript_exe="rscript-fake",
        llm_mode="off",
    )

    assert result.status == "success"
    assert result.checkpoint_path is not None
    checkpoint_data = json.loads(Path(result.checkpoint_path).read_text(encoding="utf-8"))
    assert checkpoint_data["llm_mode_requested"] == "off"
    assert checkpoint_data["llm_mode_effective"] == "off"
    assert "unique_asv_count" in checkpoint_data


def test_checkpoint_does_not_prompt_when_llm_mode_is_not_live(tmp_path):
    calls = []

    def _fake_confirm(prompt_text):
        calls.append(prompt_text)
        return True, "nao deveria ter sido chamado"

    result = run(
        _write_valid_input(tmp_path),
        runs_dir=tmp_path / "runs",
        r_runner=_fake_success_runner_with_evidence,
        rscript_exe="rscript-fake",
        llm_mode="mock",
        confirm_llm_stage=_fake_confirm,
    )

    assert result.status == "success"
    assert calls == []  # so pergunta quando llm_mode == "live"


def test_checkpoint_prompts_and_downgrades_to_off_when_declined(tmp_path):
    def _fake_confirm(prompt_text):
        return False, "usuario optou por pular (teste)"

    result = run(
        _write_valid_input(tmp_path),
        runs_dir=tmp_path / "runs",
        r_runner=_fake_success_runner_with_evidence,
        rscript_exe="rscript-fake",
        llm_mode="live",
        confirm_llm_stage=_fake_confirm,
    )

    assert result.status == "success"
    assert result.llm_mode == "off"
    checkpoint_data = json.loads(Path(result.checkpoint_path).read_text(encoding="utf-8"))
    assert checkpoint_data["llm_mode_requested"] == "live"
    assert checkpoint_data["llm_mode_effective"] == "off"
    assert "pular" in checkpoint_data["decision"]


def test_checkpoint_prompts_and_proceeds_when_confirmed(tmp_path):
    calls = []

    def _fake_confirm(prompt_text):
        calls.append(prompt_text)
        return True, "usuario confirmou (teste)"

    result = run(
        _write_valid_input(tmp_path),
        runs_dir=tmp_path / "runs",
        r_runner=_fake_success_runner_with_evidence,
        rscript_exe="rscript-fake",
        llm_mode="live",  # fixture nao tem nenhuma ASV que precise de revisao -> sem rede real
        confirm_llm_stage=_fake_confirm,
    )

    assert result.status == "success"
    assert len(calls) == 1
    assert result.llm_mode == "live"
    assert result.llm_reviewed_count == 0  # nenhuma ASV precisou de revisao nesta fixture


def test_checkpoint_assume_yes_skips_the_question(tmp_path):
    calls = []

    def _fake_confirm(prompt_text):
        calls.append(prompt_text)
        return False, "nao deveria ter sido chamado"

    result = run(
        _write_valid_input(tmp_path),
        runs_dir=tmp_path / "runs",
        r_runner=_fake_success_runner_with_evidence,
        rscript_exe="rscript-fake",
        llm_mode="live",
        confirm_llm_stage=_fake_confirm,
        assume_yes=True,
    )

    assert result.status == "success"
    assert calls == []
    assert result.llm_mode == "live"


def _write_valid_input(tmp_path: Path) -> Path:
    df = _valid_input_df()
    input_csv = tmp_path / "entrada.csv"
    _write_input_csv(input_csv, df)
    return input_csv


def test_failed_when_r_exits_nonzero(tmp_path):
    df = _valid_input_df()
    input_csv = tmp_path / "entrada.csv"
    _write_input_csv(input_csv, df)

    def _failing_runner(rscript_exe, input_csv, output_csv, config_path, timeout):
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="erro fatal no R")

    result = run(
        input_csv,
        runs_dir=tmp_path / "runs",
        r_runner=_failing_runner,
        rscript_exe="rscript-fake",
    )

    assert result.status == "failed"
    assert result.r_exit_code == 1
    assert "erro fatal no R" in result.r_stderr_tail
    assert result.output_path is None


def test_failed_when_output_row_count_mismatches(tmp_path):
    df = _valid_input_df(n_rows=3)
    input_csv = tmp_path / "entrada.csv"
    _write_input_csv(input_csv, df)

    def _short_output_runner(rscript_exe, input_csv, output_csv, config_path, timeout):
        out_df = pd.read_csv(input_csv, sep=";", decimal=",", encoding="utf-8").head(1)
        _add_curation_columns(out_df).to_csv(output_csv, sep=";", decimal=",", index=False, encoding="utf-8")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    result = run(
        input_csv,
        runs_dir=tmp_path / "runs",
        r_runner=_short_output_runner,
        rscript_exe="rscript-fake",
    )

    assert result.status == "failed"
    assert any("Numero de linhas" in issue for issue in result.verification_issues)


def test_failed_when_output_missing_curation_columns(tmp_path):
    df = _valid_input_df()
    input_csv = tmp_path / "entrada.csv"
    _write_input_csv(input_csv, df)

    def _incomplete_runner(rscript_exe, input_csv, output_csv, config_path, timeout):
        out_df = pd.read_csv(input_csv, sep=";", decimal=",", encoding="utf-8")
        out_df.to_csv(output_csv, sep=";", decimal=",", index=False, encoding="utf-8")
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    result = run(
        input_csv,
        runs_dir=tmp_path / "runs",
        r_runner=_incomplete_runner,
        rscript_exe="rscript-fake",
    )

    assert result.status == "failed"
    assert any("ausente" in issue for issue in result.verification_issues)


def test_failed_when_rscript_not_found(tmp_path, monkeypatch):
    df = _valid_input_df()
    input_csv = tmp_path / "entrada.csv"
    _write_input_csv(input_csv, df)

    monkeypatch.setattr("harness.orchestrator.find_rscript", lambda: None)

    result = run(input_csv, runs_dir=tmp_path / "runs")

    assert result.status == "failed"
    assert result.error is not None and "Rscript" in result.error
