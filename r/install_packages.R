# Instala todos os pacotes R necessarios pro curadoria_deterministica.R/.qmd.
# Rode uma vez, antes da primeira execucao: Rscript r/install_packages.R

options(repos = c(CRAN = "https://cloud.r-project.org"))

cran_packages <- c("tidyverse", "yaml", "taxize", "ape", "rgbif", "vegan", "BiocManager")
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

cat("Pacotes R prontos.\n")
