"""Relatorio HTML unico por execucao (estilo MultiQC) -- so gerado quando a
analise ecologica roda (`--ecologia` ou `--ecologia-somente`, ver
orchestrator.py). Autocontido: DataTables.js/CSS e jQuery vendorizados em
`harness/assets/` sao lidos e inlineados direto no `<head>` do HTML gerado
(nunca referenciados como link externo), entao o relatorio final abre e
funciona sem internet e sem depender de nenhum outro arquivo.

Cada secao do template degrada graciosamente ("Secao nao disponivel") em vez
de quebrar a geracao inteira quando falta o dado que ela precisa -- o
contexto vem de uma execucao real, cujas etapas podem nao ter todas rodado
(ex. `--ecologia-somente` nao tem tabela de entrada bruta nem diagnosticos
do determinístico; `--llm-mode=off` nao tem relatorio narrativo).
"""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from typing import Optional

import markdown as markdown_lib
import pandas as pd
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# (nome do arquivo sem extensao gerado por r/analise_ecologica.qmd, titulo pro relatorio)
ECOLOGIA_PLOT_FILES = [
    ("diversidade_alfa", "Diversidade alfa (riqueza, Shannon, Simpson)"),
    ("curva_acumulacao", "Curva de acumulação (esforço amostral)"),
    ("dissimilaridade_dendrograma", "Dissimilaridade entre pontos"),
    ("composicao_taxonomica", "Composição taxonômica"),
]

# Colunas candidatas a "faixa de datas" no resumo geral -- nenhuma e
# obrigatoria em nenhum schema do projeto, entao isto e so um bonus quando
# um dataset especifico trouxer uma coluna assim reconhecivel pelo nome.
DATE_COLUMN_CANDIDATES = ["Data", "Date", "Data da coleta", "Collection date"]


def _read_asset(name: str) -> str:
    return (ASSETS_DIR / name).read_text(encoding="utf-8")


def _dataframe_to_table(df: Optional[pd.DataFrame], table_id: str) -> Optional[dict]:
    if df is None or len(df) == 0:
        return None
    clean = df.astype(object).where(pd.notna(df), "")
    return {
        "id": table_id,
        "columns": [str(c) for c in df.columns],
        "rows": clean.values.tolist(),
    }


def _metadata_summary(df: Optional[pd.DataFrame]) -> Optional[dict]:
    if df is None or len(df) == 0:
        return None

    summary: dict = {}
    if "Sample" in df.columns:
        summary["amostras"] = int(df["Sample"].nunique(dropna=True))
    if "Type" in df.columns:
        tipo = df["Type"].astype(str).str.strip().str.casefold()
        summary["amostras_reais"] = int((tipo == "sample").sum())
        summary["controles"] = int((tipo != "sample").sum())
    # So Latitude/Longitude aqui -- fazem parte do contrato universal de
    # entrada (usadas pela checagem regional no GBIF, ver DOMINIO_E_CONTRATO.md),
    # entao sao relevantes pra qualquer projeto. "Habitat"/"Rios" (removidos
    # daqui) eram nomes especificos do dataset de demonstracao eDNA_Cipo --
    # so o default de `ecologia.metadados_grupo` no config, nao um contrato
    # universal; apareciam como "ausente" em qualquer outro projeto (ex.
    # roots_metabar) que nao usa essas categorias, o que so confundia.
    for col in ("Latitude", "Longitude"):
        summary[f"tem_{col.lower()}"] = bool(col in df.columns and df[col].notna().any())

    for col in DATE_COLUMN_CANDIDATES:
        if col not in df.columns:
            continue
        parsed = pd.to_datetime(df[col], errors="coerce")
        if parsed.notna().any():
            summary["data_min"] = str(parsed.min().date())
            summary["data_max"] = str(parsed.max().date())
        break

    return summary


def _format_datetime(iso_text: Optional[str]) -> Optional[str]:
    """`started_at`/`finished_at` chegam como datetime ISO 8601 completo
    (ex. "2026-09-16T04:18:51.546069+00:00") -- exibir isso cru na tela nao
    ajuda ninguem; formata pro padrao brasileiro, sem microssegundos, com o
    fuso horario por extenso (sempre UTC, ver `datetime.now(timezone.utc)`
    em orchestrator.py)."""
    if not iso_text:
        return None
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError:
        return iso_text
    return parsed.strftime("%d/%m/%Y %H:%M:%S UTC")


def _render_markdown(text: Optional[str]) -> Optional[Markup]:
    """Converte o relatorio narrativo (markdown) pro HTML renderizado --
    sem isso, o texto aparece na pagina com a sintaxe markdown literal
    (`**negrito**`, `# titulo`) em vez de formatada. `Markup` sinaliza pro
    Jinja2 (autoescape=True) que este HTML ja e confiavel e nao deve ser
    escapado de novo -- foi gerado aqui mesmo, nao e texto de terceiros."""
    if not text:
        return None
    return Markup(markdown_lib.markdown(text, extensions=["tables", "fenced_code", "sane_lists"]))


def _ecologia_plots(ecologia_output_dir: Optional[str]) -> list[dict]:
    if not ecologia_output_dir:
        return []
    out_dir = Path(ecologia_output_dir)
    if not out_dir.is_dir():
        return []

    plots = []
    for stem, title in ECOLOGIA_PLOT_FILES:
        html_path = out_dir / f"{stem}.html"
        png_path = out_dir / f"{stem}.png"
        if html_path.exists():
            plots.append({"title": title, "kind": "html", "content": html_path.read_text(encoding="utf-8")})
        elif png_path.exists():
            b64 = base64.b64encode(png_path.read_bytes()).decode("ascii")
            plots.append({"title": title, "kind": "png", "content": f"data:image/png;base64,{b64}"})
    return plots


def build_html_report(context: dict) -> str:
    """Monta o relatorio HTML unico de uma execucao a partir de um dict de
    contexto (chaves usadas, todas opcionais -- ver orchestrator.py pra como
    ele e montado em cada um dos dois caminhos, `run()` e
    `run_ecologia_somente()`):

      tipo_execucao ("completo" | "ecologia_somente"), started_at, finished_at,
      researcher, project, primer, input_df, diagnostics (dict do
      *_diagnostics.json), deterministic_df, report_text,
      llm_divergence_examples, llm_reviewed_count, llm_total_unique_asvs,
      final_df, ecologia_output_dir, ecologia_requested.

    Nunca levanta excecao por dado faltando -- cada secao do template vira
    "Secao nao disponivel" no lugar."""
    # autoescape=True explicito (nao select_autoescape por extensao) --
    # o template se chama "report.html.j2", cuja ultima extensao e ".j2",
    # entao select_autoescape(["html"]) nao o reconheceria e deixaria o
    # autoescape desligado (bug real encontrado e corrigido nesta sessao:
    # sem isso, o conteudo de cada grafico interativo, embutido via
    # srcdoc, vazava tags <html>/<head> sem escapar pro documento externo).
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template = env.get_template("report.html.j2")

    tipo_execucao = context.get("tipo_execucao", "completo")
    input_df = context.get("input_df")
    final_df = context.get("final_df")

    return template.render(
        # Markup(): com autoescape=True, {{ }} escaparia estes assets como se
        # fossem texto comum (ex. "&&" vira "&amp;&amp;" dentro do <script>),
        # corrompendo o JS/CSS vendorizado -- bug real encontrado nesta sessao,
        # o jQuery/DataTables nunca chegava a rodar de fato (SyntaxError no
        # console), entao nenhuma tabela tinha a interatividade/estilo do
        # DataTables. Seguro marcar como confiavel: sao arquivos vendorizados
        # localmente (harness/assets/), nunca conteudo de terceiros/usuario.
        datatables_css=Markup(_read_asset("dataTables.dataTables.min.css")),
        jquery_js=Markup(_read_asset("jquery.min.js")),
        datatables_js=Markup(_read_asset("dataTables.min.js")),
        tipo_execucao=tipo_execucao,
        is_somente=tipo_execucao == "ecologia_somente",
        started_at=_format_datetime(context.get("started_at")),
        finished_at=_format_datetime(context.get("finished_at")),
        researcher=context.get("researcher"),
        project=context.get("project"),
        primer=context.get("primer"),
        metadata_summary=_metadata_summary(input_df if input_df is not None else final_df),
        input_table=_dataframe_to_table(input_df, "dt-tabela-entrada"),
        diagnostics=context.get("diagnostics"),
        deterministic_table=_dataframe_to_table(context.get("deterministic_df"), "dt-tabela-deterministica"),
        report_html=_render_markdown(context.get("report_text")),
        llm_divergence_examples=context.get("llm_divergence_examples") or [],
        llm_reviewed_count=context.get("llm_reviewed_count"),
        llm_total_unique_asvs=context.get("llm_total_unique_asvs"),
        final_table=_dataframe_to_table(final_df, "dt-tabela-final"),
        ecologia_plots=_ecologia_plots(context.get("ecologia_output_dir")),
        ecologia_requested=bool(context.get("ecologia_requested")),
    )
