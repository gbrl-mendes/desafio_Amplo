"""Testes de harness.report_generation.

O caller da API (Groq) e sempre injetado ou testado via modo "mock" --
estes testes nunca fazem uma chamada de rede real nem exigem
GROQ_API_KEY configurada.
"""

from __future__ import annotations

import pandas as pd

from harness.report_generation import (
    ReportContext,
    build_report_prompt,
    build_report_stats,
    generate_report,
)


def _base_row(**overrides) -> dict:
    row = {
        "ASV header": ">ASV_1-168bp",
        "BLAST ID": "Astyanax lacustris",
        "Identification": "Astyanax lacustris",
        "Identification Max. taxonomy": "Species",
        "Contamination status": "True detection",
        "Primer expected length": "in range",
        "Assisted ID (LLM)": pd.NA,
        "Assisted Confidence (LLM)": pd.NA,
        "Assisted Justification (LLM)": pd.NA,
    }
    row.update(overrides)
    return row


def _context(**overrides) -> ReportContext:
    ctx = ReportContext(
        input_path="entrada.csv",
        row_count=1,
        started_at="2026-09-14T00:00:00+00:00",
        finished_at="2026-09-14T00:01:00+00:00",
        llm_reviewed_count=0,
        llm_total_unique_asvs=1,
    )
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


def test_build_report_stats_counts_by_column():
    df = pd.DataFrame(
        [
            _base_row(**{"ASV header": ">ASV_1"}),
            _base_row(**{"ASV header": ">ASV_2", "Identification Max. taxonomy": "Genus"}),
        ]
    )

    stats = build_report_stats(df, _context(row_count=2, llm_total_unique_asvs=2))

    assert stats["identification_counts"] == {"Species": 1, "Genus": 1}
    assert stats["contamination_counts"] == {"True detection": 2}
    assert stats["amplicon_counts"] == {"in range": 2}
    assert stats["llm_divergence_count"] == 0
    assert stats["llm_divergence_examples"] == []


def test_build_report_stats_detects_divergence():
    df = pd.DataFrame(
        [
            _base_row(
                **{
                    "ASV header": ">ASV_1",
                    "Assisted ID (LLM)": "Astyanax sp.",
                    "Assisted Confidence (LLM)": "Média",
                    "Assisted Justification (LLM)": "Sem registro regional no GBIF.",
                }
            ),
        ]
    )

    stats = build_report_stats(df, _context())

    assert stats["llm_divergence_count"] == 1
    assert stats["llm_divergence_examples"][0]["Assisted ID (LLM)"] == "Astyanax sp."


def test_build_report_prompt_includes_divergence_case():
    df = pd.DataFrame(
        [
            _base_row(
                **{
                    "ASV header": ">ASV_1",
                    "Assisted ID (LLM)": "Astyanax sp.",
                    "Assisted Confidence (LLM)": "Média",
                    "Assisted Justification (LLM)": "Sem registro regional no GBIF.",
                }
            ),
        ]
    )
    stats = build_report_stats(df, _context())

    prompt = build_report_prompt(stats)

    assert "Astyanax sp." in prompt
    assert "Sem registro regional no GBIF." in prompt
    assert "não invente" in prompt


def test_generate_report_off_mode():
    df = pd.DataFrame([_base_row()])

    report_text, result = generate_report(df, _context(), mode="off")

    assert report_text is None
    assert result.mode == "off"
    assert result.skipped_reason is not None


def test_generate_report_live_without_key_degrades_to_off(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr("harness.report_generation.load_env", lambda *a, **k: {})
    df = pd.DataFrame([_base_row()])

    report_text, result = generate_report(df, _context(), mode="live", api_key=None)

    assert report_text is None
    assert result.mode == "off"
    assert "GROQ_API_KEY" in (result.skipped_reason or "")


def test_generate_report_mock_mode_needs_no_key():
    df = pd.DataFrame([_base_row()])

    report_text, result = generate_report(df, _context(), mode="mock")

    assert result.mode == "mock"
    assert report_text is not None
    assert "[mock]" in report_text


def test_generate_report_live_with_fake_caller_returns_its_text():
    df = pd.DataFrame([_base_row()])
    captured = {}

    def fake_caller(prompt: str, api_key: str, model: str) -> str:
        captured["prompt"] = prompt
        assert api_key == "fake-key"
        return "# Relatório\n\nTexto gerado pelo caller falso."

    report_text, result = generate_report(df, _context(), mode="live", api_key="fake-key", caller=fake_caller)

    assert result.mode == "live"
    assert report_text == "# Relatório\n\nTexto gerado pelo caller falso."
    assert "entrada.csv" in captured["prompt"]


def test_generate_report_records_error_without_raising():
    df = pd.DataFrame([_base_row()])

    def failing_caller(prompt: str, api_key: str, model: str) -> str:
        raise RuntimeError("timeout simulado")

    report_text, result = generate_report(df, _context(), mode="live", api_key="fake-key", caller=failing_caller)

    assert report_text is None
    assert result.error is not None
    assert "timeout simulado" in result.error
