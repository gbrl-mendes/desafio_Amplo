# %% [markdown]
# ## Exploração/depuração de `tools/schema_validation.py`
#
# Abra este arquivo no Positron e rode célula por célula: o Positron mostra um
# link "Run Cell" acima de cada bloco marcado com `# %%` (ou use Ctrl+Enter /
# Shift+Enter). As variáveis ficam vivas na sessão entre uma célula e outra e
# aparecem no painel Variables — mesmo fluxo que você já usa com chunks de
# Quarto/RStudio. Clique em uma variável no painel Variables (ou rode a
# variável sozinha numa célula) para abrir no Data Explorer, equivalente ao
# `View()` do RStudio.

# %%
import sys
from pathlib import Path

# Garante que a raiz do repositório está no sys.path mesmo se o Positron
# rodar este arquivo com o cwd apontando para dev/.
REPO_ROOT = Path.cwd()
if not (REPO_ROOT / "tools").exists():
    REPO_ROOT = REPO_ROOT.parent
sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

from tools.schema_validation import load_schema, validate_asv_table

schema = load_schema()
print(f"{len(schema['columns'])} colunas no contrato de schema")

# %%
# Monta uma tabela mínima válida (mesma lógica dos testes) para explorar
# primeiro o caminho "feliz".
required_cols = [c["name"] for c in schema["columns"] if c["role"] == "input" and c.get("required")]

example = {}
for col in required_cols:
    if col == "ASV (Sequence)":
        example[col] = ["ACGTACGT1", "ACGTACGT2"]
    elif "indentity" in col or "qcovhsp" in col:
        example[col] = [95.0, 88.0]
    elif "staxid" in col:
        example[col] = [9606, 9606]
    else:
        example[col] = ["valor_exemplo", "valor_exemplo"]

df = pd.DataFrame(example)
df  # coloque o cursor aqui e rode a célula para ver a tabela no Data Explorer

# %%
report = validate_asv_table(df, schema=schema)
print(report.summary())
assert report.is_valid

# %%
# Quebra de propósito: injeta uma coluna de gabarito (vazamento de dado).
df_leak = df.copy()
df_leak["Curated ID"] = "Astyanax lacustris"

report_leak = validate_asv_table(df_leak, schema=schema)
print(report_leak.summary())
assert not report_leak.is_valid

# %%
# Duplica a chave primária de propósito.
df_dup = df.copy()
df_dup.loc[1, "ASV (Sequence)"] = df_dup.loc[0, "ASV (Sequence)"]

report_dup = validate_asv_table(df_dup, schema=schema)
print(report_dup.summary())
assert not report_dup.is_valid

# %%
# Simula o bug documentado de múltiplos controles separados por ";" numa
# célula (roots_metabar). É warning, não bloqueia a execução.
df_multi = df.copy()
df_multi["Ext. Control"] = ["SC_bColA", "SC_bColA;SC_bColB"]

report_multi = validate_asv_table(df_multi, schema=schema)
print(report_multi.summary())
assert report_multi.is_valid

# %%
# Para ler sua planilha real (quando o input reduzido estiver em
# data/input/), descomente e ajuste o caminho:
# real_df = pd.read_excel(REPO_ROOT / "data" / "input" / "seu_arquivo.xlsx")
# report_real = validate_asv_table(real_df, schema=schema)
# print(report_real.summary())
