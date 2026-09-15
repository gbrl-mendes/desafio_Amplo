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
    build_traditional_evidence_text,
    extract_groq_error_message,
    load_traditional_species,
    needs_review,
    run_llm_assisted_curation,
)


class _FakeResponse:
    """Stub minimo de requests.Response -- so o que extract_groq_error_message usa."""

    def __init__(self, status_code: int, json_body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json_body = json_body
        self.text = text

    def json(self):
        if self._json_body is None:
            raise ValueError("no JSON body")
        return self._json_body


def test_extract_groq_error_message_uses_error_message_field():
    resp = _FakeResponse(429, json_body={"error": {"message": "Rate limit reached for model X"}})

    message = extract_groq_error_message(resp)

    assert message == "429: Rate limit reached for model X"


def test_extract_groq_error_message_falls_back_to_raw_text_without_json():
    resp = _FakeResponse(500, json_body=None, text="<html>Internal Server Error</html>")

    message = extract_groq_error_message(resp)

    assert message.startswith("500: ")
    assert "<html>" in message


def _base_row(**overrides) -> dict:
    row = {
        "Type": "Sample",
        "Sample": "SC1A",
        "Ponto": "SC1",
        "Habitat": "Cânion",
        "Rios": "Mascate",
        "ASV header": ">ASV_1-168bp",
        "Primer": "MiFish2",
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
        "Primer expected length": "in range",
        "Possible target taxon": True,
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


def test_needs_review_false_when_amplicon_out_of_range_even_if_unreliable():
    # Amplicon fora do tamanho esperado (provavel primer-dimer/artefato) --
    # a ASV ja e descartada por isso, pedir identificacao da LLM nao ajuda.
    row = pd.Series(_base_row(**{"Primer expected length": "out of range", "BLAST ID": "Match_not_reliable"}))
    assert needs_review(row) is False


def test_needs_review_false_when_not_target_taxon():
    row = pd.Series(_base_row(**{"Possible target taxon": False, "Identification Max. taxonomy": "Genus"}))
    assert needs_review(row) is False


def test_needs_review_false_when_any_sample_flagged_possible_contamination():
    row = pd.Series(
        _base_row(**{"n_amostras_possivel_contaminacao": 1, "Identification Max. taxonomy": "Genus"})
    )
    assert needs_review(row) is False


def test_needs_review_ignores_read_origin():
    # Read origin nao existe em todo projeto (ex. exemplo_2, so plantas via
    # ITS2) -- o filtro de qualidade nao deve depender dela, mesmo quando a
    # coluna vem presente com um valor que nao seria "merged".
    row = pd.Series(_base_row(**{"Identification Max. taxonomy": "Genus", "Read origin": "R1"}))
    assert needs_review(row) is True


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


def test_build_asv_evidence_type_is_case_insensitive():
    rows = [
        _base_row(Sample="SC1A", **{"Type": "sample"}),
        _base_row(Sample="SC1B", **{"Type": "Sample"}),
        _base_row(Sample="SC1C", **{"Type": "SAMPLE"}),
    ]
    df = _make_df(rows)

    evidence = build_asv_evidence(df)

    assert len(evidence) == 1
    assert evidence.iloc[0]["n_amostras_detectada"] == 3


def test_build_asv_evidence_habitat_rios_optional_when_absent():
    # exemplo_2 nao tem os slots de metadado que viram Habitat/Rios -- a
    # agregacao nao pode quebrar so porque essas colunas nao existem.
    rows = [{k: v for k, v in _base_row().items() if k not in ("Habitat", "Rios")}]
    df = _make_df(rows)

    evidence = build_asv_evidence(df)

    assert len(evidence) == 1
    assert evidence.iloc[0]["habitats"] == []
    assert evidence.iloc[0]["rios"] == []


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


def test_build_traditional_evidence_text_without_table():
    assert "não disponível" in build_traditional_evidence_text(["SC1"], None)


def test_build_traditional_evidence_text_marks_uncovered_point_as_no_comparison_data():
    traditional_df = pd.DataFrame(
        [{"Ponto": "SC1", "Taxon_binomial": "Astyanax lacustris"}]
    )

    text = build_traditional_evidence_text(["SC1", "SC5"], traditional_df)

    assert "SC1: Astyanax lacustris" in text
    assert "SC5: sem cobertura tradicional (sem dado de comparação)" in text


def test_build_traditional_evidence_text_lists_species_for_covered_point():
    traditional_df = pd.DataFrame(
        [
            {"Ponto": "SC1", "Taxon_binomial": "Astyanax lacustris"},
            {"Ponto": "SC1", "Taxon_binomial": "Brycon nattereri"},
        ]
    )

    text = build_traditional_evidence_text(["SC1"], traditional_df)

    assert text == "SC1: Astyanax lacustris, Brycon nattereri"


def test_run_llm_assisted_curation_passes_traditional_evidence_into_prompt():
    df = _make_df([_base_row(**{"Identification Max. taxonomy": "Genus"})])
    traditional_df = pd.DataFrame(
        [{"Ponto": "SC1", "Taxon_binomial": "Astyanax lacustris"}]
    )
    captured_prompt = {}

    def fake_caller(prompt: str, api_key: str, model: str) -> dict:
        captured_prompt["prompt"] = prompt
        return {
            "assisted_id": "Astyanax lacustris",
            "assisted_confidence": "Alta",
            "assisted_justification": "Bate com registro tradicional no mesmo ponto.",
        }

    run_llm_assisted_curation(
        df,
        mode="live",
        api_key="fake-key",
        caller=fake_caller,
        traditional_species_df=traditional_df,
    )

    assert "SC1: Astyanax lacustris" in captured_prompt["prompt"]


def test_load_traditional_species_ignores_column_names(tmp_path):
    # O nome literal das colunas e desprezivel -- so a posicao importa
    # (primeira = ponto, segunda = taxon), pra aceitar arquivos de outros
    # projetos sem exigir nome de coluna especifico.
    path = tmp_path / "referencia.csv"
    path.write_text("Local;Taxa\nSC1;Astyanax lacustris\nSC2;Brycon nattereri\n", encoding="utf-8")

    df = load_traditional_species(path)

    assert list(df.columns) == ["Ponto", "Taxon_binomial"]
    assert df.iloc[0]["Ponto"] == "SC1"
    assert df.iloc[0]["Taxon_binomial"] == "Astyanax lacustris"


def test_load_traditional_species_rejects_single_column(tmp_path):
    path = tmp_path / "referencia.csv"
    path.write_text("Ponto\nSC1\n", encoding="utf-8")

    with pytest.raises(ValueError, match="pelo menos 2 colunas"):
        load_traditional_species(path)


def test_load_traditional_species_uses_taxon_binomial_column_by_name_when_present(tmp_path):
    # Tabela real do exemplo_1 tem 5 colunas (Ponto, Taxon, Genero, Epiteto,
    # Taxon_binomial) -- as do meio sao a decomposicao do nome cientifico,
    # devem ser ignoradas em favor da coluna ja pronta chamada Taxon_binomial.
    path = tmp_path / "referencia.csv"
    path.write_text(
        "Ponto;Taxon;Genero;Epiteto;Taxon_binomial\n"
        "SC1;Astyanax lacustris;Astyanax;lacustris;Astyanax lacustris\n",
        encoding="utf-8",
    )

    df = load_traditional_species(path)

    assert list(df.columns) == ["Ponto", "Taxon_binomial"]
    assert df.iloc[0]["Taxon_binomial"] == "Astyanax lacustris"


def test_load_traditional_species_falls_back_to_last_column_without_taxon_binomial_name(tmp_path):
    path = tmp_path / "referencia.csv"
    path.write_text("Ponto;Taxon;Extra\nSC1;Astyanax lacustris;x\n", encoding="utf-8")

    df = load_traditional_species(path)

    assert list(df.columns) == ["Ponto", "Taxon_binomial"]
    assert df.iloc[0]["Taxon_binomial"] == "x"
