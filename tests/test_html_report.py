"""Testes de harness.html_report.

build_html_report nunca deve levantar excecao por dado faltando no contexto
-- cada secao do template vira "Secao nao disponivel" no lugar. Estes testes
nunca rodam R nem chamam a Groq, so montam contextos e conferem o HTML.
"""

from __future__ import annotations

import pandas as pd

from harness.html_report import build_html_report

_DF = pd.DataFrame(
    {
        "Sample": ["SC1A", "SC1B"],
        "Type": ["Sample", "Sample"],
        "Ponto": ["SC1", "SC1"],
        "Identification": ["Astyanax lacustris", "Bos taurus"],
        "Curated ID": ["Astyanax lacustris", "Bos taurus"],
    }
)


def _full_context(**overrides) -> dict:
    base = dict(
        tipo_execucao="completo",
        started_at="2026-09-15T20:00:00Z",
        finished_at="2026-09-15T20:05:00Z",
        researcher="Gabriel",
        project="eDNA_Cipo",
        primer="MiFish2",
        input_df=_DF,
        diagnostics={
            "refinamento_hits": {"selected_hit_origin": "1: 2", "match_not_reliable_count": 0, "total_linhas": 2},
            "checagem_regional": {"gbif_consultadas": 2, "pulada": False},
        },
        deterministic_df=_DF,
        report_text="Relatorio narrativo de teste.",
        llm_divergence_examples=[
            {
                "ASV header": ">ASV_1",
                "BLAST ID": "Bos taurus",
                "Identification": "Bos taurus",
                "Assisted ID (LLM)": "Astyanax lacustris",
                "Assisted Confidence (LLM)": "media",
                "Assisted Justification (LLM)": "Vizinhos filogeneticos proximos de especie alvo.",
            }
        ],
        llm_reviewed_count=1,
        llm_total_unique_asvs=2,
        final_df=_DF,
        ecologia_output_dir=None,
        ecologia_requested=True,
    )
    base.update(overrides)
    return base


def test_full_context_renders_all_eight_sections_without_raising():
    html = build_html_report(_full_context())

    assert "<html" in html and "</html>" in html
    # 1. Cabecalho
    assert "Gabriel" in html and "eDNA_Cipo" in html and "MiFish2" in html
    # 2. Resumo geral
    assert "Resumo geral" in html
    # 3. Tabela de input bruto
    assert 'id="tabela-entrada"' in html
    # 4. Resumos do determinístico (diagnostics)
    assert "Refinamento dos hits de BLAST" in html
    assert "Checagem regional" in html
    # 5. Tabela de saida do deterministico
    assert 'id="tabela-deterministica"' in html
    # 6. Curadoria assistida (narrativo + divergencia)
    assert "Relatorio narrativo de teste." in html
    assert "Vizinhos filogeneticos proximos de especie alvo." in html
    # 7. Tabela final
    assert 'id="tabela-final"' in html
    # 8. Analise ecologica (pedida, mas sem plots -- "nao disponivel")
    assert "Análise ecológica" in html


def test_ecologia_somente_context_renders_enxuta_version_with_warning():
    html = build_html_report(
        dict(
            tipo_execucao="ecologia_somente",
            started_at="2026-09-15T20:00:00Z",
            final_df=_DF,
            ecologia_requested=True,
        )
    )

    assert "Esta análise partiu de um CSV já curado" in html
    # Secoes que so existem numa execucao completa nao devem aparecer.
    assert 'id="tabela-entrada"' not in html
    assert 'id="tabela-deterministica"' not in html
    # A tabela final (recebida) continua presente.
    assert 'id="tabela-final"' in html


def test_empty_context_does_not_raise_and_shows_unavailable_sections():
    html = build_html_report({})

    assert "<html" in html
    assert "Seção não disponível" in html


def test_ecologia_plots_embedded_when_html_fragment_exists(tmp_path):
    eco_dir = tmp_path / "ecologia"
    eco_dir.mkdir()
    (eco_dir / "diversidade_alfa.html").write_text("<html><body>grafico falso</body></html>", encoding="utf-8")

    html = build_html_report(_full_context(ecologia_output_dir=str(eco_dir)))

    assert "diversidade_alfa" not in html  # nome de arquivo nao deveria vazar, so o titulo
    assert "Diversidade alfa" in html
    assert "iframe" in html
    assert "grafico falso" in html  # conteudo do widget, escapado dentro do srcdoc


def test_ecologia_plots_fall_back_to_png_when_no_html(tmp_path):
    eco_dir = tmp_path / "ecologia"
    eco_dir.mkdir()
    # PNG minimo valido (1x1 pixel) so pra existir como arquivo.
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
        "de0000000c4944415478da6360606060000000050001a5f645400000000049454e44ae426082"
    )
    (eco_dir / "diversidade_alfa.png").write_bytes(png_bytes)

    html = build_html_report(_full_context(ecologia_output_dir=str(eco_dir)))

    assert "data:image/png;base64," in html
    assert "Diversidade alfa" in html
