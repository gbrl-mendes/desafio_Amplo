"""Testes de harness.progress.

`default_confirm_llm_stage` nunca deve realmente esperar por stdin nestes
testes -- cada cenario mocka `sys.stdin.isatty()`/`input()` explicitamente.
"""

from __future__ import annotations

import pandas as pd
import pytest

from harness.progress import (
    build_checkpoint_stats,
    default_confirm_llm_stage,
    format_checkpoint_summary,
    format_input_summary,
)


def _base_row(**overrides) -> dict:
    row = {
        "Researcher": "Gabriel",
        "Project": "eDNA_Cipo",
        "Type": "Sample",
        "Sample": "SC1A",
        "Ponto": "SC1",
        "Habitat": "Cânion",
        "Rios": "Mascate",
        "ASV (Sequence)": "ACGTACGT",
        "ASV header": ">ASV_1-168bp",
        "1_subject header": "Astyanax lacustris",
        "1_staxid": 9606,
        "1_indentity": 99.5,
        "1_qcovhsp": 98.0,
        "BLAST ID": "Astyanax lacustris",
        "Identification": "Astyanax lacustris",
        "Identification Max. taxonomy": "Species",
        "BLASTn pseudo-score": 99.5,
        "Selected_Hit_Origin": "1",
        "Genus (NCBI)": "Astyanax",
        "Family (NCBI)": "Characidae",
        "Order (NCBI)": "Characiformes",
        "Class (NCBI)": "Actinopteri",
        "Vizinhos filogeneticos (k)": ">ASV_2-168bp",
        "GBIF regional occurrence count": 42,
        "Possible target taxon": True,
        "Read origin": "merged",
        "Contamination status": "True detection",
        "Primer expected length": "in range",
    }
    row.update(overrides)
    return row


def test_format_input_summary_includes_researcher_project_and_counts():
    df = pd.DataFrame([_base_row(), _base_row(Sample="SC1B", **{"ASV (Sequence)": "TTTTAAAA"})])

    summary = format_input_summary(df, reference_csv=None)

    assert "Gabriel" in summary
    assert "eDNA_Cipo" in summary
    assert "Linhas (sequencia x amostra): 2" in summary
    assert "Sequencias unicas (ASVs): 2" in summary
    assert "nenhuma" in summary.lower()


def test_format_input_summary_notes_reference_file_when_given():
    df = pd.DataFrame([_base_row()])

    summary = format_input_summary(df, reference_csv="data/example/spp_tradicional.csv")

    assert "spp_tradicional.csv" in summary


def test_format_input_summary_explains_asvs_exclusive_to_controls():
    # Uma ASV que so aparece num controle (nunca detectada numa amostra
    # real) faz o total divergir do que o checkpoint (Type == "Sample")
    # vai contar depois -- a mensagem tem que explicar isso, nao esconder.
    df = pd.DataFrame(
        [
            _base_row(**{"ASV (Sequence)": "AAAA"}),
            _base_row(**{"ASV (Sequence)": "TTTT", "Type": "Ext. Control", "Sample": "SC_ctrl"}),
        ]
    )

    summary = format_input_summary(df, reference_csv=None)

    assert "2 no total" in summary
    assert "1 detectada(s) em amostra(s) real(is)" in summary
    assert "1 exclusiva(s) de controle" in summary


def test_format_input_summary_warns_on_multiple_researchers_or_projects():
    df = pd.DataFrame([_base_row(Researcher="Gabriel"), _base_row(Researcher="Outra Pessoa")])

    summary = format_input_summary(df, reference_csv=None)

    assert "Aviso" in summary


def test_build_checkpoint_stats_counts_by_column():
    df = pd.DataFrame(
        [
            _base_row(**{"ASV header": ">ASV_1"}),
            _base_row(**{"ASV header": ">ASV_2", "Identification Max. taxonomy": "Genus"}),
        ]
    )

    stats = build_checkpoint_stats(df)

    assert stats["row_count"] == 2
    assert stats["unique_asv_count"] == 2
    assert stats["identification_counts"] == {"Species": 1, "Genus": 1}
    assert stats["needs_review_count"] == 1  # so o "Genus" precisa de revisao


def test_format_checkpoint_summary_mentions_needs_review_count():
    # Linha confiante (especie, GBIF > 0, hit confiavel) -- 0 de 1 precisa de revisao.
    stats = build_checkpoint_stats(pd.DataFrame([_base_row()]))

    summary = format_checkpoint_summary(stats)

    assert "0 de 1" in summary


def test_default_confirm_llm_stage_non_interactive_proceeds_automatically(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    proceed, reason = default_confirm_llm_stage("prompt de teste")

    assert proceed is True
    assert "nao interativo" in reason.lower() or "automaticamente" in reason.lower()


def test_default_confirm_llm_stage_eof_proceeds_automatically(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def _raise_eof(*args, **kwargs):
        raise EOFError()

    monkeypatch.setattr("builtins.input", _raise_eof)

    proceed, reason = default_confirm_llm_stage("prompt de teste")

    assert proceed is True


def test_default_confirm_llm_stage_accepts_default_yes(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "")

    proceed, reason = default_confirm_llm_stage("prompt de teste")

    assert proceed is True
    assert "confirmou" in reason.lower()


def test_default_confirm_llm_stage_declines_on_no(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "n")

    proceed, reason = default_confirm_llm_stage("prompt de teste")

    assert proceed is False
    assert "pular" in reason.lower()
