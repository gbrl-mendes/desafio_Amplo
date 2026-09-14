"""Unit tests for tools.schema_validation.

Uses small synthetic fixtures built in-line (not the real spreadsheet) so
these tests run fast and don't depend on any external data file.
"""

from __future__ import annotations

import pandas as pd

from tools.schema_validation import load_column_aliases, load_schema, validate_asv_table

SCHEMA = load_schema()

# Minimal set of required-input columns pulled straight from the schema
# contract, so this fixture stays in sync if the contract changes.
REQUIRED_COLS = [c["name"] for c in SCHEMA["columns"] if c["role"] == "input" and c.get("required")]


def _base_valid_df(n_rows: int = 2) -> pd.DataFrame:
    """Build a minimal valid table containing every required column."""
    data = {}
    for col in REQUIRED_COLS:
        if col == "ASV (Sequence)":
            data[col] = [f"ACGT{i}" for i in range(n_rows)]
        elif col in ("1_indentity", "2_indentity", "3_indentity", "1_qcovhsp", "2_qcovhsp", "3_qcovhsp"):
            data[col] = [95.0] * n_rows
        elif col in ("1_staxid", "2_staxid", "3_staxid"):
            data[col] = [9606] * n_rows
        elif col in ("Sample total abundance", "ASV absolute abundance", "ASV Size (pb)"):
            data[col] = [100] * n_rows
        else:
            data[col] = [f"value_{i}" for i in range(n_rows)]
    return pd.DataFrame(data)


def test_valid_table_passes():
    df = _base_valid_df()
    report = validate_asv_table(df, schema=SCHEMA)
    assert report.is_valid, report.summary()
    assert report.blocking == []


def test_missing_required_column_is_blocking():
    df = _base_valid_df().drop(columns=["1_subject header"])
    report = validate_asv_table(df, schema=SCHEMA)
    assert not report.is_valid
    codes = [i.code for i in report.blocking]
    assert "missing_required_columns" in codes
    missing_issue = next(i for i in report.blocking if i.code == "missing_required_columns")
    assert "1_subject header" in missing_issue.details


def test_duplicate_primary_key_is_blocking():
    # Primary key is composite (ASV (Sequence) + Unique_File_name): the table
    # is long-format (one row per ASV x sample), so ASV (Sequence) alone
    # repeats legitimately across samples and must not be flagged by itself.
    df = _base_valid_df(n_rows=2)
    df.loc[1, "ASV (Sequence)"] = df.loc[0, "ASV (Sequence)"]
    df.loc[1, "Unique_File_name"] = df.loc[0, "Unique_File_name"]
    report = validate_asv_table(df, schema=SCHEMA)
    assert not report.is_valid
    codes = [i.code for i in report.blocking]
    assert "duplicate_primary_key" in codes


def test_same_asv_across_different_samples_is_not_duplicate():
    # Same ASV (Sequence) in two rows is expected (long-format table) as long
    # as Unique_File_name differs — must NOT be flagged as a duplicate key.
    df = _base_valid_df(n_rows=2)
    df.loc[1, "ASV (Sequence)"] = df.loc[0, "ASV (Sequence)"]
    assert df.loc[0, "Unique_File_name"] != df.loc[1, "Unique_File_name"]
    report = validate_asv_table(df, schema=SCHEMA)
    assert report.is_valid, report.summary()


def test_leakage_column_present_is_blocking():
    df = _base_valid_df()
    df["Curated ID"] = "Astyanax lacustris"  # gabarito, must never be in input
    report = validate_asv_table(df, schema=SCHEMA)
    assert not report.is_valid
    codes = [i.code for i in report.blocking]
    assert "leakage_columns_present" in codes
    issue = next(i for i in report.blocking if i.code == "leakage_columns_present")
    assert "Curated ID" in issue.details


def test_multi_value_control_cell_triggers_warning_not_blocking():
    df = _base_valid_df()
    df["Ext. Control"] = "SC_bColA"
    df.loc[0, "Ext. Control"] = "SC_bColA;SC_bColB"
    report = validate_asv_table(df, schema=SCHEMA)
    # This is a data-quality flag, not a reason to refuse the whole run.
    assert report.is_valid
    codes = [i.code for i in report.warnings]
    assert "multi_value_control_cell" in codes
    issue = next(i for i in report.warnings if i.code == "multi_value_control_cell")
    assert any("SC_bColA;SC_bColB" in d for d in issue.details)


def test_out_of_scope_column_present_is_only_a_warning():
    df = _base_valid_df()
    df["OTU"] = "OTU_1"
    report = validate_asv_table(df, schema=SCHEMA)
    assert report.is_valid
    codes = [i.code for i in report.warnings]
    assert "out_of_scope_columns_present" in codes


def test_column_aliases_let_a_table_with_different_raw_names_pass():
    # Same table as test_valid_table_passes, but "Researcher" arrives under
    # a project-specific raw name -- without the alias this would fail
    # missing_required_columns.
    df = _base_valid_df().rename(columns={"Researcher": "Pesquisador"})
    report = validate_asv_table(df, schema=SCHEMA, column_aliases={"Pesquisador": "Researcher"})
    assert report.is_valid, report.summary()


def test_column_aliases_do_not_mutate_caller_dataframe():
    df = _base_valid_df().rename(columns={"Researcher": "Pesquisador"})
    validate_asv_table(df, schema=SCHEMA, column_aliases={"Pesquisador": "Researcher"})
    assert "Pesquisador" in df.columns  # renamed on a copy, not the original


def test_column_alias_raw_name_missing_is_a_warning_not_blocking():
    df = _base_valid_df()
    report = validate_asv_table(df, schema=SCHEMA, column_aliases={"Nome_Que_Nao_Existe": "Researcher"})
    assert report.is_valid, report.summary()
    codes = [i.code for i in report.warnings]
    assert "column_alias_raw_name_not_found" in codes


def test_load_column_aliases_without_config_returns_the_default_baseline():
    # Sem --config, Python precisa aplicar o mesmo default que o R aplica
    # sozinho (DEFAULT_CONFIG$colunas_alias) -- senao uma execucao sobre o
    # dado de demonstracao passaria no R mas seria recusada aqui.
    aliases = load_column_aliases(None)
    assert aliases["Metadata 1"] == "Ponto"
    assert aliases["Metadata 8"] == "Latitude"


def test_load_column_aliases_user_config_overrides_default_baseline(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("colunas_alias:\n  Pesquisador: Researcher\n", encoding="utf-8")
    aliases = load_column_aliases(config_path)
    assert aliases["Pesquisador"] == "Researcher"
    assert aliases["Metadata 1"] == "Ponto"  # baseline continua presente


def test_load_column_aliases_missing_key_keeps_default_baseline(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("contaminacao:\n  fold_change_threshold: 5\n", encoding="utf-8")
    assert load_column_aliases(config_path) == load_column_aliases(None)


if __name__ == "__main__":
    # Allows `python tests/test_schema_validation.py` to work even without
    # pytest installed; `pytest tests/` (recommended) discovers and runs the
    # same functions normally.
    _tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    failed = 0
    for fn in _tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(_tests) - failed}/{len(_tests)} passed")
    raise SystemExit(1 if failed else 0)
