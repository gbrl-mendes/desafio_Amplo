# Roda a suite de testes R (testthat) do pipeline deterministico:
# Rscript r/tests/run_tests.R
suppressPackageStartupMessages(library(testthat))

repo_root <- rprojroot::find_root(rprojroot::has_file("DOMINIO_E_CONTRATO.md"))
results <- test_dir(file.path(repo_root, "r", "tests", "testthat"), reporter = "summary", stop_on_failure = TRUE)
