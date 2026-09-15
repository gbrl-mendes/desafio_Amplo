# Instala todos os pacotes R necessarios pro curadoria_deterministica.R/.qmd
# e pro analise_ecologica.R/.qmd. Rode uma vez, antes da primeira execucao:
# Rscript r/install_packages.R

options(repos = c(CRAN = "https://cloud.r-project.org"))

cran_packages <- c(
  "tidyverse", "yaml", "taxize", "ape", "rgbif", "vegan", "jsonlite",
  "plotly", "htmlwidgets", "ggdendro", "rmarkdown", "pandoc", "gh", "BiocManager"
)
to_install <- setdiff(cran_packages, rownames(installed.packages()))
if (length(to_install) > 0) {
  cat(sprintf("Instalando do CRAN: %s\n", paste(to_install, collapse = ", ")))
  install.packages(to_install)
}

bioc_packages <- c("DECIPHER", "Biostrings")
to_install_bioc <- setdiff(bioc_packages, rownames(installed.packages()))
if (length(to_install_bioc) > 0) {
  cat(sprintf("Instalando do Bioconductor: %s\n", paste(to_install_bioc, collapse = ", ")))
  BiocManager::install(to_install_bioc, update = FALSE, ask = FALSE)
}

# htmlwidgets::saveWidget(selfcontained = TRUE) (usado pelos graficos
# interativos de analise_ecologica.R) exige pandoc. Sem um pandoc de sistema
# ja disponivel (ex. instalado junto com RStudio/Quarto), o pacote R "pandoc"
# baixa um binario portatil isolado, sem precisar de instalacao separada.
if (!rmarkdown::pandoc_available()) {
  cat("Pandoc de sistema nao encontrado -- instalando um binario portatil via o pacote R 'pandoc'...\n")
  pandoc::pandoc_install()
}

cat("Pacotes R prontos.\n")
