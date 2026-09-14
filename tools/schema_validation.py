"""Deterministic schema/ingestion validation for the ASV input table.

This is the first stage of the pipeline: it never makes a judgment call, it
only checks facts about the incoming table against the contract defined in
``data/reference/asv_input_schema.yaml``. Everything here must be
reproducible and independent of any LLM.

Three kinds of problems are checked:

1. Missing required columns (a raw/metadata column the pipeline needs is
   absent).
2. Leakage columns present (a column that the system is supposed to compute
   for itself, or the human curator's ground truth, shows up in the input
   anyway — this would let the pipeline "cheat" instead of deriving its own
   answer, so it is always BLOCKING).
3. Data-quality edge cases documented from the real roots_metabar pipeline:
   the ASV (Sequence) primary key must be unique, and any control column
   (Ext./PCR/Filt. Control) must not silently contain more than one control
   name in a single cell separated by ";" (a documented real bug in the
   original R pipeline's join logic).

The output is a :class:`ValidationReport` that a harness can act on: BLOCKING
issues should stop the run (a "refusal"), WARNING issues should be surfaced
but do not have to stop execution.

Column names are allowed to vary between projects (each may name the same
concept differently): an optional `column_aliases` map (raw name -> canonical
name, read from the same `--config` YAML the R pipeline uses, see
``load_column_aliases``) is applied before any check below, so what "required
columns" means is judged against canonical names, not literal ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pandas as pd
import yaml

DEFAULT_SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "reference" / "asv_input_schema.yaml"
)

# Mesmo baseline de `DEFAULT_CONFIG$colunas_alias` em
# r/curadoria_deterministica.qmd -- precisa ficar em sincronia com aquele,
# porque o R nao expõe o proprio default num arquivo que o Python possa ler.
# Sem isso, uma execucao sem `--config` passaria no lado R (que aplica seu
# proprio default) mas seria recusada aqui (Python nao veria "Metadata 1"
# como "Ponto"). Um `--config` de usuario sobrescreve so as chaves que trouxer
# (mesmo merge raso que `modifyList` faz do lado R).
DEFAULT_COLUMN_ALIASES: dict[str, str] = {
    "Metadata 1": "Ponto",
    "Metadata 8": "Latitude",
    "Metadata 9": "Longitude",
    "Metadata 10": "Habitat",
    "Metadata 11": "Rios",
}

IssueLevel = Literal["blocking", "warning"]


@dataclass
class ValidationIssue:
    level: IssueLevel
    code: str
    message: str
    details: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def blocking(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "blocking"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def is_valid(self) -> bool:
        """True if there is nothing blocking. Warnings are still allowed through."""
        return len(self.blocking) == 0

    def add(self, level: IssueLevel, code: str, message: str, details: list[str] | None = None) -> None:
        self.issues.append(ValidationIssue(level=level, code=code, message=message, details=details or []))

    def summary(self) -> str:
        lines = [f"Validation report — {'OK' if self.is_valid else 'BLOCKED'}"]
        for issue in self.issues:
            lines.append(f"  [{issue.level.upper()}] {issue.code}: {issue.message}")
            for d in issue.details[:10]:
                lines.append(f"      - {d}")
            if len(issue.details) > 10:
                lines.append(f"      ... (+{len(issue.details) - 10} more)")
        return "\n".join(lines)


def load_schema(schema_path: str | Path = DEFAULT_SCHEMA_PATH) -> dict:
    with open(schema_path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_column_aliases(config_path: str | Path | None) -> dict[str, str]:
    """Le o mapa `colunas_alias` (nome bruto -> nome canonico) de um YAML de
    config, o mesmo arquivo passado via `--config` pro R (`DEFAULT_CONFIG` em
    curadoria_deterministica.qmd), por cima do baseline em
    ``DEFAULT_COLUMN_ALIASES``. Sem config, retorna so o baseline -- mesmo
    default que o R aplica sozinho, para as duas linguagens se comportarem
    igual sobre o dado de demonstracao mesmo sem `--config`."""
    aliases = dict(DEFAULT_COLUMN_ALIASES)
    if config_path is None:
        return aliases
    with open(config_path, encoding="utf-8") as fh:
        user_config = yaml.safe_load(fh) or {}
    aliases.update(user_config.get("colunas_alias") or {})
    return aliases


def _columns_by_role(schema: dict, role: str) -> list[str]:
    return [c["name"] for c in schema["columns"] if c["role"] == role]


def validate_asv_table(
    df: pd.DataFrame,
    schema: dict | None = None,
    column_aliases: dict[str, str] | None = None,
) -> ValidationReport:
    """Validate an ASV table against the input schema contract.

    Parameters
    ----------
    df:
        The raw ASV table exactly as it would be handed to the agent.
    schema:
        Pre-loaded schema dict. If omitted, loads ``DEFAULT_SCHEMA_PATH``.
    column_aliases:
        Optional map of raw column name -> canonical column name (same
        `colunas_alias` config the R pipeline applies -- see
        ``load_column_aliases``). Applied to a copy of ``df`` before any
        other check, so a table whose columns aren't already named
        canonically can still pass validation. An alias whose raw name
        isn't present in ``df`` is reported as a warning, not silently
        ignored, so a typo in the config is visible without reading logs.
    """
    if schema is None:
        schema = load_schema()

    report = ValidationReport()

    if column_aliases:
        present_aliases = {raw: canonical for raw, canonical in column_aliases.items() if raw in df.columns}
        absent_aliases = [raw for raw in column_aliases if raw not in df.columns]
        if absent_aliases:
            report.add(
                level="warning",
                code="column_alias_raw_name_not_found",
                message=(
                    "Coluna(s) declarada(s) em colunas_alias mas ausente(s) da tabela de "
                    "entrada -- renomeação pulada para elas."
                ),
                details=absent_aliases,
            )
        if present_aliases:
            df = df.rename(columns=present_aliases)

    required_input_cols = [c["name"] for c in schema["columns"] if c["role"] == "input" and c.get("required")]
    leakage_cols = _columns_by_role(schema, "derived_recompute") + _columns_by_role(schema, "gabarito")
    out_of_scope_cols = _columns_by_role(schema, "out_of_scope")
    primary_key = schema.get("primary_key")
    primary_key_cols = [primary_key] if isinstance(primary_key, str) else list(primary_key or [])
    multi_value_cols = schema.get("multi_value_control_columns", [])

    present = set(df.columns)

    # 1. Missing required columns.
    missing = [c for c in required_input_cols if c not in present]
    if missing:
        report.add(
            level="blocking",
            code="missing_required_columns",
            message=f"{len(missing)} coluna(s) obrigatória(s) ausente(s) da tabela de entrada.",
            details=missing,
        )

    # 2. Leakage: derived/curation columns should never be in the agent's input.
    leaked = [c for c in leakage_cols if c in present]
    if leaked:
        report.add(
            level="blocking",
            code="leakage_columns_present",
            message=(
                "Coluna(s) que o sistema deve calcular por conta própria (ou que são "
                "gabarito de curadoria humana) estão presentes na entrada — isso "
                "permitiria o sistema copiar em vez de derivar a resposta."
            ),
            details=leaked,
        )

    # Out-of-scope columns are not an error, just worth a warning so nobody is
    # surprised the system ignores them (e.g. OTU/SWARM clustering).
    present_out_of_scope = [c for c in out_of_scope_cols if c in present]
    if present_out_of_scope:
        report.add(
            level="warning",
            code="out_of_scope_columns_present",
            message="Coluna(s) fora do escopo do sistema estão presentes e serão ignoradas.",
            details=present_out_of_scope,
        )

    # 3. Primary key uniqueness (may be a single column or a composite list).
    missing_pk_cols = [c for c in primary_key_cols if c not in present]
    if primary_key_cols and not missing_pk_cols:
        dup_mask = df.duplicated(subset=primary_key_cols, keep=False)
        if dup_mask.any():
            dup_values = sorted(
                {" | ".join(str(v) for v in row) for row in df.loc[dup_mask, primary_key_cols].itertuples(index=False)}
            )
            pk_label = " + ".join(primary_key_cols)
            report.add(
                level="blocking",
                code="duplicate_primary_key",
                message=f"Chave primária '{pk_label}' não é única — {len(dup_values)} combinação(ões) duplicada(s).",
                details=dup_values,
            )
    elif missing_pk_cols:
        # Already covered by missing_required_columns if those columns were
        # required, but guard against a schema edit that un-marks them.
        report.add(
            level="blocking",
            code="missing_primary_key",
            message=f"Coluna(s) de chave primária ausente(s) da tabela: {', '.join(missing_pk_cols)}.",
        )

    # 4. Documented edge case: multiple controls in one cell separated by ";".
    for col in multi_value_cols:
        if col not in present:
            continue
        series = df[col].dropna().astype(str)
        offending_rows = series[series.str.contains(";")]
        if not offending_rows.empty:
            examples = [f"linha {idx}: '{val}'" for idx, val in offending_rows.items()]
            report.add(
                level="warning",
                code="multi_value_control_cell",
                message=(
                    f"Coluna '{col}' contém célula(s) com múltiplos controles separados "
                    "por ';' — isso quebra a suposição de 'um controle por categoria' "
                    "usada no cálculo de fold-change (bug documentado no pipeline "
                    "roots_metabar original). Precisa de tratamento explícito antes do "
                    "join, não de um merge simples."
                ),
                details=examples,
            )

    return report
