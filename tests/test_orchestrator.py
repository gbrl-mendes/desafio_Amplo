"""Testes de harness.orchestrator.

Usa um "R runner" falso (injetado via parametro `r_runner`) em vez de
chamar Rscript de verdade -- estes testes nunca tocam rede (NCBI/GBIF) nem
dependem de R instalado, so verificam a logica de orquestracao (validar ->
rodar -> verificar -> logar).
"""

from __future__ import annotations

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
    )

    assert result.status == "success"
    assert result.output_path == str(output_csv)
    assert output_csv.exists()


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
