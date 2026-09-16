# Roda antes de qualquer test-*.R (convencao testthat pra arquivos
# "helper-*.R"): faz source() do pipeline determinístico so pra reusar as
# definicoes de funcao (ex. gbif_regional_count, run_regional_check), sem
# disparar main() nem exigir <entrada.csv> na linha de comando.
Sys.setenv(CURADORIA_TESTING = "true")

repo_root <- rprojroot::find_root(rprojroot::has_file("DOMINIO_E_CONTRATO.md"))
source(file.path(repo_root, "r", "curadoria_deterministica.R"), chdir = FALSE)
