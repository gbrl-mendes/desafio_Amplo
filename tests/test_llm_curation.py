"""Testes de harness.llm_curation.

O caller da API (Groq) e sempre injetado ou testado via modo "mock" --
estes testes nunca fazem uma chamada de rede real nem exigem
GROQ_API_KEY configurada.
"""

from __future__ import annotations

import pandas as pd
import pytest

from harness.llm_curation import (
    ASSISTED_COLUMNS,
    build_asv_evidence,
    needs_review,
    run_llm_assisted_curation,
)


def _base_row(**overrides) -> dict:
    row = {
        "Type": "Sample",
        "Sample": "SC1A",
        "Ponto": "SC1",
        "Habitat": "Cânion",
        "Rios": "Mascate",
        "ASV header": ">ASV_1-168bp",
        "BLAST ID": "Astyanax lacustris",
        "Identification": "Astyanax lacustris",
        "Identification Max. taxonomy": "Species",
        "BLASTn pseudo-score": 99.5,
        "Selected_Hit_Origin": "1",
        "Genus (NCBI)": "Astyanax",
        "Family (NCBI)": "Characidae",
        "Order (NCBI)": "Characiformes",
        "Class (NCBI)": "Actinopteri",
        "Vizinhos filogeneticos (k)": ">ASV_2-168bp; >ASV_3-167bp",
        "GBIF regional occurrence count": 42,
        "Contamination status": "True detection",
    }
    row.update(overrides)
    return row


def _make_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_needs_review_true_for_match_not_reliable():
    row = pd.Series(_base_row(**{"BLAST ID": "Match_not_reliable"}))
    assert needs_review(row) is True


def test_needs_review_true_for_non_species_level():
    row = pd.Series(_base_row(**{"Identification Max. taxonomy": "Genus"}))
    assert needs_review(row) is True


def test_needs_review_true_for_species_with_zero_gbif():
    row = pd.Series(_base_row(**{"GBIF regional occurrence count": 0}))
    assert needs_review(row) is True


def test_needs_review_true_for_species_with_na_gbif():
    row = pd.Series(_base_row(**{"GBIF regional occurrence count": float("nan")}))
    assert needs_review(row) is True


def test_needs_review_false_for_confident_species():
    row = pd.Series(_base_row())
    assert needs_review(row) is False


def test_build_asv_evidence_excludes_controls_and_aggregates():
    rows = [
        _base_row(Sample="SC1A", Ponto="SC1"),
        _base_row(Sample="SC1B", Ponto="SC1", **{"Contamination status": "Possible contamination"}),
        _base_row(Type="Ext. Control", Sample="SC_bExt_0802", Ponto="SCctrl"),
    ]
    df = _make_df(rows)

    evidence = build_asv_evidence(df)

    assert len(evidence) == 1
    row = evidence.iloc[0]
    assert row["n_amostras_detectada"] == 2
    assert row["n_amostras_possivel_contaminacao"] == 1
    assert row["pontos"] == ["SC1"]  # controle (Ponto=SCctrl) nao entra


def test_run_llm_assisted_curation_off_mode_fills_na():
    df = _make_df([_base_row()])

    result_df, result = run_llm_assisted_curation(df, mode="off")

    assert result.mode == "off"
    assert result.skipped_reason is not None
    for col in ASSISTED_COLUMNS:
        assert result_df[col].isna().all()


def test_run_llm_assisted_curation_live_without_key_degrades_to_off(monkeypatch):
    # Isola de qualquer GROQ_API_KEY real (env var do sistema ou o .env do
    # repo, que pode ja ter uma chave de verdade do Gabriel) -- este teste
    # verifica especificamente o caminho "sem nenhuma chave disponivel", por
    # isso troca load_env() por um no-op em vez de deixar ler o .env real.
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr("harness.llm_curation.load_env", lambda *a, **k: {})
    df = _make_df([_base_row(**{"Identification Max. taxonomy": "Genus"})])

    result_df, result = run_llm_assisted_curation(df, mode="live", api_key=None)

    assert result.mode == "off"
    assert "GROQ_API_KEY" in (result.skipped_reason or "")


def test_run_llm_assisted_curation_reviews_only_uncertain_asvs_and_propagates():
    rows = [
        _base_row(Sample="SC1A", **{"ASV header": ">ASV_confiavel"}),
        _base_row(Sample="SC1A", **{"ASV header": ">ASV_incerta", "Identification Max. taxonomy": "Genus"}),
        _base_row(Sample="SC1B", **{"ASV header": ">ASV_incerta", "Identification Max. taxonomy": "Genus"}),
    ]
    df = _make_df(rows)

    def fake_caller(prompt: str, api_key: str, model: str) -> dict:
        assert api_key == "fake-key"
        return {
            "assisted_id": "Astyanax sp.",
            "assisted_confidence": "Média",
            "assisted_justification": "Consenso com vizinhos filogenéticos.",
        }

    result_df, result = run_llm_assisted_curation(
        df, mode="live", api_key="fake-key", caller=fake_caller
    )

    assert result.total_unique_asvs == 2
    assert result.reviewed_count == 1  # so a ASV incerta precisou de revisao

    confiavel_rows = result_df[result_df["ASV header"] == ">ASV_confiavel"]
    assert confiavel_rows["Assisted ID (LLM)"].isna().all()

    incerta_rows = result_df[result_df["ASV header"] == ">ASV_incerta"]
    assert (incerta_rows["Assisted ID (LLM)"] == "Astyanax sp.").all()
    assert (incerta_rows["Assisted Confidence (LLM)"] == "Média").all()
    # propagou pras 2 linhas (SC1A e SC1B) da mesma ASV
    assert len(incerta_rows) == 2


def test_run_llm_assisted_curation_records_error_without_failing_others():
    rows = [
        _base_row(**{"ASV header": ">ASV_com_erro", "Identification Max. taxonomy": "Genus"}),
    ]
    df = _make_df(rows)

    def failing_caller(prompt: str, api_key: str, model: str) -> dict:
        raise RuntimeError("timeout simulado")

    result_df, result = run_llm_assisted_curation(
        df, mode="live", api_key="fake-key", caller=failing_caller
    )

    assert len(result.errors) == 1
    assert "timeout simulado" in result.errors[0]
    assert result_df["Assisted ID (LLM)"].isna().all()
    assert "Erro" in result_df["Assisted Justification (LLM)"].iloc[0]


def test_run_llm_assisted_curation_mock_mode_needs_no_key():
    df = _make_df([_base_row(**{"Identification Max. taxonomy": "Genus"})])

    result_df, result = run_llm_assisted_curation(df, mode="mock")

    assert result.mode == "mock"
    assert result.reviewed_count == 1
    assert result_df["Assisted Justification (LLM)"].iloc[0].startswith("[mock]")
