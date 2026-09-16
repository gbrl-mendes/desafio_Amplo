"""Testes de harness.__main__ (parsing de linha de comando e ramificacao).

Nunca chama orchestrator.run/run_ecologia_somente de verdade -- so confere
que os argumentos de CLI sao validados e roteados corretamente antes de
qualquer execucao real comecar.
"""

from __future__ import annotations

import pytest

import sys

from harness.__main__ import build_parser, main, warn_if_store_python


def test_parser_accepts_missing_input_csv_at_parse_time():
    # input_csv e nargs="?" -- o parser sozinho aceita rodar sem ele; a
    # checagem "nenhum dos dois informados" mora em main(), testada abaixo.
    args = build_parser().parse_args(["--ecologia-somente", "curado.csv"])
    assert args.input_csv is None
    assert args.ecologia_somente_csv == "curado.csv"


def test_main_errors_when_neither_input_nor_ecologia_somente_given(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main([])

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "ecologia-somente" in captured.err or "entrada.csv" in captured.err


def test_main_errors_when_both_input_and_ecologia_somente_given(tmp_path, capsys):
    fake_csv = tmp_path / "entrada.csv"
    fake_csv.write_text("Researcher;Project\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        main([str(fake_csv), "--ecologia-somente", str(fake_csv)])

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "mutuamente exclusiv" in captured.err


def test_main_routes_to_ecologia_somente_without_running_full_pipeline(tmp_path, monkeypatch):
    curado_csv = tmp_path / "curado.csv"
    curado_csv.write_text(
        "Ponto;Sample;Type;Curated ID;ASV absolute abundance\nSC1;SC1A;Sample;Astyanax lacustris;100\n",
        encoding="utf-8",
    )

    calls = {"run": 0, "run_ecologia_somente": 0}

    def _fake_run(*args, **kwargs):
        calls["run"] += 1
        raise AssertionError("run() completo nao deveria ser chamado com --ecologia-somente")

    class _FakeResult:
        status = "success"
        validation_summary = "ok"
        error = None
        r_exit_code = 0
        r_stderr_tail = ""
        ecologia_output_dir = str(tmp_path / "runs" / "fake_ecologia")
        html_report_path = None

    def _fake_run_ecologia_somente(**kwargs):
        calls["run_ecologia_somente"] += 1
        return _FakeResult()

    monkeypatch.setattr("harness.__main__.run", _fake_run)
    monkeypatch.setattr("harness.__main__.run_ecologia_somente", _fake_run_ecologia_somente)

    exit_code = main(["--ecologia-somente", str(curado_csv)])

    assert exit_code == 0
    assert calls == {"run": 0, "run_ecologia_somente": 1}


def test_warn_if_store_python_warns_when_base_prefix_is_windowsapps(monkeypatch, capsys):
    monkeypatch.setattr(
        sys, "base_prefix", r"C:\Users\Gabriel\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13"
    )

    warn_if_store_python()

    captured = capsys.readouterr()
    assert "Microsoft Store" in captured.err
    assert "python.org" in captured.err


def test_warn_if_store_python_silent_for_a_regular_install(monkeypatch, capsys):
    monkeypatch.setattr(sys, "base_prefix", r"C:\Users\Gabriel\AppData\Local\Programs\Python\Python313")

    warn_if_store_python()

    captured = capsys.readouterr()
    assert captured.err == ""
