"""Renderiza o relatorio narrativo (markdown gerado por LLM, ver
report_generation.py) como PDF. O LLM continua escrevendo o relatorio em
markdown normalmente -- este modulo so converte esse markdown (via HTML
intermediario) pro documento final entregue ao usuario.

O CSS abaixo (`_REPORT_CSS`) comecou como o tema "Cayman" do pacote R
`prettydoc` (https://prettydoc.statr.me/cayman.html), mas foi migrado pra
paleta e tipografia da marca Amplo -- mesmas cores/fonte de
harness/templates/report.html.j2 (ver `:root` la), pra PDF e HTML nao
divergirem visualmente. Fonte: system font stack (nao um arquivo de fonte
baixado), mesma razao do relatorio HTML -- ver `body` abaixo. Antes disso, um
`<link>` pro Google Fonts (removido nesta migracao) era a UNICA dependencia
de rede da geracao de PDF, nao documentada em nenhum lugar; eliminar a
fonte externa elimina essa dependencia por completo em vez de so documenta-la.

Renderizado via um navegador Chromium local (Edge no Windows -- vem
instalado por padrao em qualquer Windows 10/11 -- ou Chrome) em modo
headless, nao por um motor de PDF em Python: o banner usa um gradiente CSS
que motores como `xhtml2pdf` nao suportam (foi tentado nesta sessao e o
resultado saiu claramente errado), e so um motor de renderizacao real de
browser reproduz o tema com fidelidade.
"""

from __future__ import annotations

import html
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import markdown as markdown_lib

_REPORT_CSS = """
/* Cores da marca Amplo -- mesmos tokens de harness/templates/report.html.j2
   (:root), pra PDF e HTML terem a mesma identidade visual. */
:root {
  --brand-blue: #116994;
  --brand-green: #009235;
  --text: #4a4f54;
  --border: #e3e5e7;
  --stripe: #f6f7f8;
}
/* Paisagem: o relatorio narrativo costuma ter tabelas largas (ex. divergencias
   da curadoria assistida, com uma coluna de justificativa longa) que não cabem
   em A4 retrato sem espremer o conteudo. */
@page { size: A4 landscape; margin: 0; }
/* O tema original (prettydoc/Cayman, de onde este veio) foi feito pra tela
   (16px), grande demais pra uma pagina impressa. Reduzir aqui em vez de no
   `body` escala TUDO que usa `rem` (titulos, espacamento, celulas de tabela)
   proporcionalmente, porque `rem` e relativo ao font-size do elemento raiz
   (`html`), nao do `body`. */
html { font-size: 13px; }
* { box-sizing: border-box; }
body {
  padding: 0; margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size: 1rem; line-height: 1.5; color: var(--text);
}
a { color: var(--brand-blue); text-decoration: none; }
a:hover { text-decoration: underline; }
.page-header {
  color: #fff; text-align: center;
  background-image: linear-gradient(120deg, var(--brand-blue), var(--brand-green));
  padding: 1.5rem 2rem;
}
.page-header :last-child { margin-bottom: 0.5rem; }
.project-name { margin-top: 0; margin-bottom: 0.1rem; font-size: 2rem; font-weight: 700; }
.project-tagline { margin-bottom: 0.5rem; font-weight: normal; opacity: 0.7; font-size: 1.5rem; }
.project-author, .project-date { font-weight: normal; opacity: 0.7; font-size: 1.2rem; }
.main-content { max-width: 74rem; padding: 1.5rem 2.5rem 2.5rem; margin: 0 auto; font-size: 1.1rem; }
.main-content :first-child { margin-top: 0; }
.main-content img { max-width: 100%; }
.main-content h1, .main-content h2, .main-content h3,
.main-content h4, .main-content h5, .main-content h6 {
  margin-top: 2rem; margin-bottom: 1rem; font-weight: 600; color: var(--brand-blue);
}
.main-content p { margin-bottom: 1em; }
.main-content code {
  padding: 2px 4px; font-family: Consolas, "Liberation Mono", Menlo, Courier, monospace;
  color: #567482; background-color: var(--stripe); border-radius: 0.3rem;
}
.main-content pre {
  padding: 0.8rem; margin-top: 0; margin-bottom: 1rem;
  font: 1rem Consolas, "Liberation Mono", Menlo, Courier, monospace;
  color: #567482; word-wrap: normal; background-color: var(--stripe);
  border: solid 1px var(--border); border-radius: 0.3rem; line-height: 1.45; overflow: auto;
}
.main-content pre > code {
  padding: 0; margin: 0; color: #567482; word-break: normal;
  white-space: pre; background: transparent; border: 0;
}
.main-content ul, .main-content ol { margin-top: 0; }
.main-content blockquote {
  padding: 0 1rem; margin-left: 0; color: #819198;
  border-left: 0.3rem solid var(--border); font-size: 1.2rem;
}
.main-content blockquote > :first-child { margin-top: 0; }
.main-content blockquote > :last-child { margin-bottom: 0; }
.main-content table {
  /* table-layout: fixed divide a largura igualmente entre as colunas (em vez
     do "auto" tentar manter celulas curtas sem quebra e espremer a mais
     longa) -- generico o bastante pra qualquer tabela que o LLM gerar,
     sem precisar saber de antemao quantas colunas ela vai ter. */
  width: 100%; table-layout: fixed; border-collapse: collapse; border-spacing: 0; margin: 1rem 0;
}
.main-content table th { font-weight: bold; background-color: var(--brand-blue); color: #fff; }
.main-content table th, .main-content table td {
  padding: 0.4rem 0.7rem; border-bottom: 1px solid var(--border); text-align: left;
  word-wrap: break-word; overflow-wrap: break-word;
}
.main-content table tr:nth-child(odd) { background-color: var(--stripe); }
.main-content dl { padding: 0; }
.main-content dl dt { padding: 0; margin-top: 1rem; font-size: 1rem; font-weight: bold; }
.main-content dl dd { padding: 0; margin-bottom: 1rem; }
.main-content hr { height: 2px; padding: 0; margin: 1rem 0; background-color: var(--stripe); border: 0; }
"""

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>{css}</style>
</head>
<body>
<div class="page-header">
  <h1 class="project-name">{title}</h1>
  {tagline_html}
</div>
<div class="main-content">
{body_html}
</div>
</body>
</html>
"""


def find_chromium_browser() -> Optional[str]:
    """Localiza um navegador Chromium local (Edge no Windows, ou Chrome) --
    usado tanto pra imprimir o HTML como PDF em modo headless (abaixo)
    quanto pra abrir o relatorio HTML automaticamente numa janela nova ao
    final da execucao (ver harness/orchestrator.py::open_report_in_browser).
    Edge vem instalado por padrao em qualquer Windows 10/11, entao
    normalmente nao precisa instalar nada a mais so pra estes recursos."""
    for exe_name in ("msedge", "chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(exe_name)
        if found:
            return found

    windows_candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for candidate in windows_candidates:
        if Path(candidate).exists():
            return candidate
    return None


def markdown_to_pdf_bytes(markdown_text: str, title: str, subtitle: str | None = None) -> bytes:
    """Converte um texto markdown (normalmente o relatorio narrativo gerado
    por LLM em `report_generation.py`) num PDF no tema da marca Amplo, igual
    ao do relatorio HTML (ver `_REPORT_CSS` acima).

    Usa um navegador Chromium local em modo headless pra imprimir o HTML --
    da fidelidade total ao CSS (gradiente do banner). Fonte via system font
    stack (nao um arquivo baixado) -- ao contrario de uma versao anterior
    deste modulo, gerar o PDF nao depende de internet pra nada.
    """
    browser = find_chromium_browser()
    if browser is None:
        raise RuntimeError(
            "Nenhum navegador Chromium (Edge/Chrome) encontrado no sistema -- necessario "
            "pra renderizar o relatorio em PDF. Instale o Microsoft Edge (vem por padrao "
            "no Windows) ou o Google Chrome."
        )

    body_html = markdown_lib.markdown(markdown_text, extensions=["tables", "fenced_code", "sane_lists"])
    tagline_html = f'<h2 class="project-tagline">{html.escape(subtitle)}</h2>' if subtitle else ""
    page_html = _PAGE_TEMPLATE.format(
        css=_REPORT_CSS,
        title=html.escape(title),
        tagline_html=tagline_html,
        body_html=body_html,
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        html_path = Path(tmp_dir) / "relatorio.html"
        pdf_path = Path(tmp_dir) / "relatorio.pdf"
        html_path.write_text(page_html, encoding="utf-8")

        args = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            # Perfil isolado, so pra esta chamada: sem isso, se o Edge ja tiver
            # uma sessao rodando em segundo plano (comportamento padrao do
            # Windows -- "continuar executando apps em segundo plano"), esta
            # invocacao so repassa os argumentos pra sessao existente e
            # termina de imediato (exit code 0), sem imprimir nada, porque
            # essa sessao ja aberta nao esta em modo headless.
            f"--user-data-dir={Path(tmp_dir) / 'profile'}",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ]
        result = subprocess.run(args, capture_output=True, text=True, timeout=60)
        if not pdf_path.exists():
            raise RuntimeError(
                f"Falha ao renderizar o relatorio em PDF via '{browser}' "
                f"(codigo de saida {result.returncode}): {result.stderr[:500]}"
            )
        return pdf_path.read_bytes()
