"""Curadoria assistida por LLM -- etapa pos-deterministica.

Le a tabela ja curada pelo curadoria_deterministica.R e, para ASVs cuja
identificacao determinada nao foi conclusiva (ver `needs_review`), consulta
um LLM (hoje: Groq, API compativel com OpenAI) pedindo uma sugestao de
identificacao, confianca e justificativa -- SEM nunca sobrescrever o
resultado deterministico (`Identification` / `BLAST ID`). O resultado vira
3 colunas novas, lado a lado com as deterministicas, permitindo auditar as
duas camadas separadamente:
  - `Assisted ID (LLM)`
  - `Assisted Confidence (LLM)`
  - `Assisted Justification (LLM)`

Roda por ASV UNICA (agrupada por `ASV header`), nao por linha: a mesma ASV
se repete em varias amostras com a mesma evidencia de BLAST/taxonomia, so
mudando o contexto de onde foi detectada -- revisar uma vez e propagar
evita chamadas de API redundantes.

Degrada graciosamente sem GROQ_API_KEY configurada: preenche as 3 colunas
com NA e um aviso, sem falhar o resto do pipeline (mesmo espirito de
`find_rscript()` em orchestrator.py). Um modo "mock" tambem esta disponivel
para testar o encadeamento sem gastar cota de API nem precisar de rede.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
# O catalogo de modelos da Groq muda com frequencia (ver .env / README pra
# como trocar sem editar codigo). openai/gpt-oss-120b: maior modelo de texto
# disponivel na conta gratuita no momento em que isto foi escrito, com
# suporte a structured_outputs/json_mode/reasoning -- bom encaixe pro
# raciocinio de curadoria que pedimos aqui.
DEFAULT_MODEL = "openai/gpt-oss-120b"

ASSISTED_COLUMNS = [
    "Assisted ID (LLM)",
    "Assisted Confidence (LLM)",
    "Assisted Justification (LLM)",
]

EVIDENCE_COLUMNS = [
    "BLAST ID",
    "Identification",
    "Identification Max. taxonomy",
    "BLASTn pseudo-score",
    "Selected_Hit_Origin",
    "Genus (NCBI)",
    "Family (NCBI)",
    "Order (NCBI)",
    "Class (NCBI)",
    "Vizinhos filogeneticos (k)",
    "GBIF regional occurrence count",
]

PROMPT_TEMPLATE = """Você é um especialista em curadoria taxonômica de dados de eDNA \
(metabarcoding) de peixes da Serra do Cipó (bacia do rio São Francisco, Minas Gerais, \
Brasil), primer MiFish2. Você está revisando uma ASV cuja identificação determinística \
ficou incerta ou levantou alguma suspeita geográfica/de contaminação. Analise as \
evidências abaixo -- já calculadas por um pipeline determinístico (BLAST, taxonomia NCBI, \
árvore filogenética, GBIF) -- e decida uma identificação final defensável, um nível de \
confiança e uma justificativa curta. Não invente evidência que não esteja listada abaixo.

EVIDÊNCIAS:
- Identificação bruta do BLAST (melhor hit informativo): {blast_id}
- Identificação após filtro de pseudo-score: {identification} (nível alcançado: {max_taxonomy})
- Pseudo-score do BLAST (0-100, quanto maior mais confiável o alinhamento): {pseudo_score}
- Hit do BLAST usado (1=melhor hit; 2/3=hit alternativo, usado porque o(s) hit(s) melhor(es) \
não eram informativos, ex. "Uncultured organism"): {hit_origin}
- Taxonomia NCBI disponível: Gênero={genus}, Família={family}, Ordem={order}, Classe={classe}
- Vizinhos filogenéticos mais próximos na árvore ASV-contra-ASV (k=5, cada um com seu próprio \
identificador de ASV -- consulte a identificação de cada vizinho na mesma tabela se precisar): \
{neighbors}
- Registros dessa espécie no GBIF dentro da bacia do rio São Francisco / Serra do Cipó: \
{gbif_count} (NA = não consultado, pois a identificação não chegou a nível de espécie)
- Detectada em {n_samples} amostra(s) reais; marcada como possível contaminação (Fold Change \
baixo vs. controle) em {n_contam} dessas
- Ponto(s) amostral(is): {pontos} | Habitat(s): {habitats} | Rio(s): {rios}

TAREFA: Responda em português, em JSON estrito, sem nenhum texto fora do JSON, com exatamente estas chaves:
{{
  "assisted_id": "nome científico ou nível taxonômico mais específico que você considera \
defensável dado o conjunto de evidências, ou 'Unidentified' se não houver evidência suficiente",
  "assisted_confidence": "Alta, Média ou Baixa",
  "assisted_justification": "1 a 3 frases explicando o raciocínio, citando as evidências acima \
que pesaram na decisão"
}}
"""


def load_env(env_path: Path = ENV_PATH) -> dict[str, str]:
    """Carrega pares KEY=VALUE de um arquivo .env pro ambiente do processo.

    Uma variavel de ambiente ja existente tem prioridade sobre o .env --
    permite que quem for rodar o harness sobrescreva com a propria chave
    sem precisar editar o arquivo (ex. `$env:GROQ_API_KEY=...` antes de
    rodar).
    """
    import os

    loaded: dict[str, str] = {}
    if not env_path.exists():
        return loaded

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        loaded[key] = value
        os.environ.setdefault(key, value)

    return loaded


@dataclass
class LlmCurationResult:
    mode: str  # "live" | "mock" | "off"
    reviewed_count: int = 0
    total_unique_asvs: int = 0
    errors: list[str] = field(default_factory=list)
    skipped_reason: Optional[str] = None


def needs_review(row: pd.Series) -> bool:
    """True se a identificacao deterministica dessa ASV nao foi conclusiva
    o bastante pra dispensar uma segunda opiniao: sem hit confiavel, nivel
    abaixo de especie, ou especie sem nenhum registro regional no GBIF."""
    if row.get("BLAST ID") == "Match_not_reliable":
        return True
    if row.get("Identification Max. taxonomy") != "Species":
        return True
    gbif_count = row.get("GBIF regional occurrence count")
    if pd.isna(gbif_count) or gbif_count == 0:
        return True
    return False


def build_asv_evidence(df: pd.DataFrame) -> pd.DataFrame:
    """Agrega o dataframe (long-format, uma linha por ASV x amostra) numa
    linha por ASV unica, com a evidencia relevante pra curadoria assistida.
    So considera amostras reais (Type == "Sample"), nao os proprios
    controles."""
    sample_rows = df[df["Type"] == "Sample"].copy()

    agg_spec = {col: (col, "first") for col in EVIDENCE_COLUMNS}
    agg_spec["n_amostras_detectada"] = ("Sample", "nunique")
    agg_spec["n_amostras_possivel_contaminacao"] = (
        "Contamination status",
        lambda s: int((s == "Possible contamination").sum()),
    )
    agg_spec["pontos"] = ("Ponto", lambda s: sorted(s.dropna().astype(str).unique().tolist()))
    agg_spec["habitats"] = ("Habitat", lambda s: sorted(s.dropna().astype(str).unique().tolist()))
    agg_spec["rios"] = ("Rios", lambda s: sorted(s.dropna().astype(str).unique().tolist()))

    grouped = sample_rows.groupby("ASV header", dropna=False).agg(**agg_spec).reset_index()
    return grouped


def build_prompt(evidence_row: pd.Series) -> str:
    return PROMPT_TEMPLATE.format(
        blast_id=evidence_row["BLAST ID"],
        identification=evidence_row["Identification"],
        max_taxonomy=evidence_row["Identification Max. taxonomy"],
        pseudo_score=evidence_row["BLASTn pseudo-score"],
        hit_origin=evidence_row["Selected_Hit_Origin"],
        genus=evidence_row["Genus (NCBI)"],
        family=evidence_row["Family (NCBI)"],
        order=evidence_row["Order (NCBI)"],
        classe=evidence_row["Class (NCBI)"],
        neighbors=evidence_row["Vizinhos filogeneticos (k)"],
        gbif_count=evidence_row["GBIF regional occurrence count"],
        n_samples=evidence_row["n_amostras_detectada"],
        n_contam=evidence_row["n_amostras_possivel_contaminacao"],
        pontos=", ".join(evidence_row["pontos"]) or "desconhecido",
        habitats=", ".join(evidence_row["habitats"]) or "desconhecido",
        rios=", ".join(evidence_row["rios"]) or "desconhecido",
    )


def _parse_llm_json(content: str) -> dict:
    """Extrai o objeto JSON da resposta do modelo, tolerando texto extra
    ao redor (alguns modelos abertos ignoram response_format ocasionalmente)."""
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(content[start : end + 1])
        raise


def call_groq(
    prompt: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    timeout: int = 60,
    max_retries: int = 4,
) -> dict:
    """Chama o endpoint de chat da Groq (compativel com OpenAI). Tenta de
    novo com backoff exponencial em erro 429 (rate limit) ou 5xx -- comuns
    numa camada gratuita -- antes de desistir."""
    last_error: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            resp = requests.post(
                f"{GROQ_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                },
                timeout=timeout,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"{resp.status_code}: {resp.text[:200]}", response=resp)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return _parse_llm_json(content)
        except (requests.RequestException, json.JSONDecodeError, KeyError, IndexError) as exc:
            last_error = exc
            if attempt < max_retries - 1:
                time.sleep(2**attempt)

    raise RuntimeError(f"Falha ao consultar a Groq apos {max_retries} tentativas: {last_error}")


def mock_llm_response(evidence_row: pd.Series) -> dict:
    """Resposta simulada, sem rede -- so pra testar o encadeamento
    (agregacao -> prompt -> merge) sem gastar cota de API real."""
    return {
        "assisted_id": evidence_row["Identification"],
        "assisted_confidence": "Baixa",
        "assisted_justification": "[mock] Resposta simulada, sem chamada real ao LLM.",
    }


LlmCaller = Callable[[str, str, str], dict]


def run_llm_assisted_curation(
    df: pd.DataFrame,
    mode: str = "live",
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    caller: Optional[LlmCaller] = None,
    verbose: bool = True,
) -> tuple[pd.DataFrame, LlmCurationResult]:
    """Executa a curadoria assistida e devolve o dataframe com as 3 colunas
    novas (propagadas pra todas as linhas de cada ASV) + um relatorio do
    que foi feito.

    `caller` e injetavel de proposito (assim como `r_runner` no
    orchestrator) -- os testes passam um caller falso pra nao depender de
    rede nem de uma chave de API real.
    """
    df = df.copy()
    for col in ASSISTED_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    if mode == "off":
        return df, LlmCurationResult(mode="off", skipped_reason="Modo 'off' explicitamente selecionado.")

    load_env()
    import os

    resolved_key = api_key or os.environ.get("GROQ_API_KEY")
    resolved_model = model or os.environ.get("GROQ_MODEL", DEFAULT_MODEL)

    if mode == "live" and not resolved_key:
        if verbose:
            print(
                "Curadoria assistida por LLM pulada -- GROQ_API_KEY nao configurada "
                "(defina no .env ou como variavel de ambiente). "
                "A curadoria deterministica permanece completa e valida."
            )
        return df, LlmCurationResult(
            mode="off",
            skipped_reason="GROQ_API_KEY nao configurada.",
        )

    evidence = build_asv_evidence(df)
    to_review = evidence[evidence.apply(needs_review, axis=1)].copy()

    result = LlmCurationResult(mode=mode, total_unique_asvs=len(evidence))

    if len(to_review) == 0:
        if verbose:
            print("Nenhuma ASV precisou de revisao assistida (todas conclusivas no determinístico).")
        return df, result

    if caller is None:
        caller = call_groq

    assisted_rows = []
    for _, evidence_row in to_review.iterrows():
        prompt = build_prompt(evidence_row)
        try:
            if mode == "mock":
                response = mock_llm_response(evidence_row)
            else:
                response = caller(prompt, resolved_key, resolved_model)
            assisted_rows.append(
                {
                    "ASV header": evidence_row["ASV header"],
                    "Assisted ID (LLM)": response.get("assisted_id"),
                    "Assisted Confidence (LLM)": response.get("assisted_confidence"),
                    "Assisted Justification (LLM)": response.get("assisted_justification"),
                }
            )
            result.reviewed_count += 1
            if verbose:
                print(f"[{mode}] {evidence_row['ASV header']}: {response.get('assisted_id')}")
        except Exception as exc:  # nao deixa uma ASV com erro derrubar as demais
            result.errors.append(f"{evidence_row['ASV header']}: {exc}")
            assisted_rows.append(
                {
                    "ASV header": evidence_row["ASV header"],
                    "Assisted ID (LLM)": pd.NA,
                    "Assisted Confidence (LLM)": pd.NA,
                    "Assisted Justification (LLM)": f"Erro na consulta ao LLM: {exc}",
                }
            )

    assisted_df = pd.DataFrame(assisted_rows)

    df = df.drop(columns=ASSISTED_COLUMNS).merge(assisted_df, on="ASV header", how="left")

    return df, result
