## Rscript curadoria_deterministica.R <entrada.csv> [--config=config.yaml] [--output=output_pos_curadoria_LLM-AAAA-MM-DD.csv]

# Garante que a biblioteca pessoal do usuario entra em .libPaths() mesmo
# quando o script e chamado como subprocesso (ex. pelo harness em Python via
# subprocess.run) -- um subprocesso nao herda necessariamente o mesmo
# ambiente de uma sessao interativa de terminal/RStudio, entao nao da pra
# depender de R_LIBS_USER ja estar exportado por quem chama.
user_lib <- Sys.getenv("R_LIBS_USER")
if (nzchar(user_lib) && dir.exists(user_lib) && !(user_lib %in% .libPaths())) {
  .libPaths(c(user_lib, .libPaths()))
}

suppressPackageStartupMessages({
  library(tidyverse)
  library(yaml)
  library(taxize)
  library(DECIPHER)
  library(ape)
  library(rgbif)
  library(jsonlite)
})

get_repo_root <- function() {
  # CURADORIA_TESTING=true: este arquivo esta sendo source()ado de dentro de
  # um test runner (r/tests/testthat), nao rodado diretamente via Rscript --
  # o `--file=` de commandArgs() apontaria pro script do test runner, nao pra
  # este arquivo, e o working directory ja pode ter sido alterado pelo
  # testthat antes de chegar aqui. Detectar a raiz do repo por um marcador de
  # arquivo em vez de tentar reconstruir o caminho a partir da linha de
  # comando evita essa confusao.
  if (identical(Sys.getenv("CURADORIA_TESTING"), "true")) {
    return(rprojroot::find_root(rprojroot::has_file("DOMINIO_E_CONTRATO.md")))
  }
  cli_args <- commandArgs(trailingOnly = FALSE)
  file_arg <- grep("^--file=", cli_args, value = TRUE)
  if (length(file_arg) > 0) {
    script_path <- normalizePath(sub("^--file=", "", file_arg[1]))
    return(dirname(dirname(script_path)))
  }
  if (requireNamespace("rstudioapi", quietly = TRUE) && rstudioapi::isAvailable()) {
    ctx_path <- tryCatch(rstudioapi::getSourceEditorContext()$path, error = function(e) "")
    if (nzchar(ctx_path)) return(dirname(dirname(normalizePath(ctx_path))))
  }
  getwd()
}

REPO_ROOT <- get_repo_root()
SCHEMA_PATH <- file.path(REPO_ROOT, "data", "reference", "asv_input_schema.yaml")

if (!identical(normalizePath(getwd()), normalizePath(REPO_ROOT))) {
  message(sprintf("Ajustando diretorio de trabalho para a raiz do repo: %s", REPO_ROOT))
  setwd(REPO_ROOT)
}

DEFAULT_CONFIG <- list(
  # Mapeia o nome bruto de qualquer coluna (como chega no CSV de um projeto
  # especifico) para o nome canonico que o resto do pipeline usa -- inclui
  # tanto colunas obrigatorias (Researcher, Project, Sample, os campos de
  # cada hit do BLAST, etc.) quanto as semanticas opcionais de metadado
  # (Ponto/Latitude/Longitude/Habitat/Rios). So precisa declarar aqui o que
  # de fato for diferente do canonico no seu arquivo -- o que nao for
  # declarado e assumido ja canonico. Colunas de latitude/longitude devem
  # ser mapeadas exatamente para "Latitude"/"Longitude": e nessas colunas
  # que a correcao de escala (grau decimal codificado como inteiro,
  # dividido por 1e6) e aplicada.
  #
  # O default abaixo so cobre o que o dataset de demonstracao deste projeto
  # (eDNA_Cipo) precisa -- os slots de metadado generico (Metadata N) nao
  # tem nome semantico proprio no CSV bruto, entao precisam ser mapeados
  # mesmo para o dado de demonstracao. Os campos obrigatorios (Researcher,
  # Project, hits do BLAST, etc.) ja vem com nome canonico nesse dataset,
  # entao nao aparecem aqui -- mas podem ser sobrescritos via --config para
  # outro projeto que use nomes diferentes.
  colunas_alias = c(
    "Metadata 1" = "Ponto",
    "Metadata 8" = "Latitude",
    "Metadata 9" = "Longitude",
    "Metadata 10" = "Habitat",
    "Metadata 11" = "Rios"
  ),
  contaminacao = list(
    fold_change_threshold = 10
  ),
  # Faixa de tamanho esperada (pb) por primer. So precisa declarar aqui o
  # primer que voce quer travar manualmente -- qualquer primer presente
  # nos dados e NAO declarado aqui tem a faixa calculada automaticamente
  # a partir da propria distribuicao de ASV Size (pb) daquele primer no
  # dataset (ver resolve_amplicon_ranges, bloco 4).
  amplicon_por_primer = list(
    MiFish2 = c(140, 200)
  ),
  identificacao = list(
    pseudoscore_thresholds = list(
      especie = 98, genero = 95, familia = 90, ordem = 80, classe = 60
    )
  ),
  arvore_filogenetica = list(
    k_vizinhos = 5
  ),
  taxons_alvo = list(
    # Nomes de grupos definidos no registro interno de taxons-alvo (bloco
    # 4). Um projeto multi-primer pode combinar mais de um grupo, ex.
    # c("Actinopteri", "Periphyton"). "Actinopteri" (peixes osseos), nao o
    # "Metazoa" generico (reino animal inteiro) -- default calibrado pro
    # dataset de demonstracao deste projeto (eDNA_Cipo, peixes).
    grupos = c("Actinopteri")
  ),
  checagem_regional = list(
    fonte = "gbif",
    # Sem area_bbox declarada aqui: por padrao, a area consultada no GBIF e
    # calculada a partir do min/max de Latitude/Longitude dos pontos
    # amostrados nos proprios dados de entrada (ver compute_area_bbox),
    # expandido por buffer_graus em cada lado. Declarar area_bbox no
    # --config (lat_min/lat_max/long_min/long_max) usa essa area fixa em
    # vez do calculo automatico.
    buffer_graus = 0.1
  )
)

load_config <- function(config_path = NULL, defaults = DEFAULT_CONFIG) {
  if (is.null(config_path)) return(defaults)
  if (!file.exists(config_path)) {
    stop(sprintf("Arquivo de config '%s' nao encontrado.", config_path), call. = FALSE)
  }
  user_config <- yaml::read_yaml(config_path)
  merged <- utils::modifyList(defaults, user_config, keep.null = TRUE)

  if (!is.null(user_config$colunas_alias)) {
    merged_alias <- utils::modifyList(
      as.list(defaults$colunas_alias), as.list(user_config$colunas_alias), keep.null = TRUE
    )
    merged$colunas_alias <- unlist(merged_alias)
  }

  merged
}

pipeline_diagnostics <- new.env()
pipeline_diagnostics$etapas <- list()

diag_add <- function(etapa, dados) {
  pipeline_diagnostics$etapas[[etapa]] <- dados
}

format_counts <- function(x, na_label = "sem dado") {
  tab <- table(x, useNA = "always")
  labels <- names(tab)
  labels[is.na(labels)] <- na_label
  paste(sprintf("%s: %d", labels, as.integer(tab)), collapse = ", ")
}

read_schema <- function(schema_path = SCHEMA_PATH) {
  yaml::read_yaml(schema_path)
}

check_required_columns <- function(df, schema) {
  required <- purrr::keep(schema$columns, function(col) {
    identical(col$role, "input") && isTRUE(col$required)
  })
  required_names <- purrr::map_chr(required, "name")
  missing <- setdiff(required_names, colnames(df))
  if (length(missing) > 0) {
    stop(
      paste0(
        "Tabela de entrada nao passa no contrato de schema -- ",
        length(missing), " coluna(s) obrigatoria(s) ausente(s):\n  - ",
        paste(missing, collapse = "\n  - "),
        "\n(Isto e so uma guarda de sanidade para uso standalone do script;",
        " a validacao completa fica em tools/schema_validation.py.)"
      ),
      call. = FALSE
    )
  }
  invisible(TRUE)
}

read_asv_table <- function(path) {
  readr::read_delim(
    path,
    delim = ";",
    locale = readr::locale(decimal_mark = ",", encoding = "UTF-8"),
    quote = "\"",
    show_col_types = FALSE,
    progress = FALSE
  )
}

apply_column_aliases <- function(df, colunas_alias) {
  rename_map <- colunas_alias

  present <- rename_map[names(rename_map) %in% colnames(df)]
  absent <- rename_map[!names(rename_map) %in% colnames(df)]
  if (length(absent) > 0) {
    warning(sprintf(
      "Coluna(s) declarada(s) em colunas_alias mas ausente(s) do arquivo, renomeacao pulada: %s",
      paste(names(absent), collapse = ", ")
    ), call. = FALSE)
  }

  if (length(present) > 0) {
    df <- dplyr::rename(df, !!!setNames(names(present), present))
  }

  # Vem como inteiro (ex. -19420314); consulta geografica (GBIF, bloco 6)
  # exige grau decimal.
  if ("Latitude" %in% colnames(df)) {
    df$Latitude <- as.numeric(df$Latitude) / 1e6
  }
  if ("Longitude" %in% colnames(df)) {
    df$Longitude <- as.numeric(df$Longitude) / 1e6
  }
  df
}

add_asv_size <- function(df) {
  dplyr::mutate(df, `ASV Size (pb)` = nchar(`ASV (Sequence)`))
}

add_sample_total_abundance <- function(df) {
  dplyr::group_by(df, Sample) %>%
    dplyr::mutate(`Sample total abundance` = sum(`ASV absolute abundance`, na.rm = TRUE)) %>%
    dplyr::ungroup()
}

add_asv_header <- function(df) {
  unique_asvs <- df %>%
    dplyr::group_by(`ASV (Sequence)`) %>%
    dplyr::summarise(
      .size = nchar(dplyr::first(`ASV (Sequence)`)),
      .total_abundance = sum(`ASV absolute abundance`, na.rm = TRUE),
      .groups = "drop"
    ) %>%
    dplyr::arrange(dplyr::desc(.size), dplyr::desc(.total_abundance)) %>%
    dplyr::mutate(.rank = dplyr::row_number())

  df %>%
    dplyr::left_join(
      dplyr::select(unique_asvs, `ASV (Sequence)`, .rank),
      by = "ASV (Sequence)"
    ) %>%
    dplyr::mutate(`ASV header` = sprintf(">ASV_%d-%dbp", .rank, `ASV Size (pb)`)) %>%
    dplyr::select(-.rank)
}

compute_derived_local_columns <- function(df) {
  df %>%
    add_asv_size() %>%
    add_sample_total_abundance() %>%
    add_asv_header()
}

run_setup_parsing <- function(input_path, config = DEFAULT_CONFIG, schema = read_schema()) {
  cat("\n[1/6] Setup e parsing: lendo o CSV, aplicando alias de colunas, checando estrutura...\n")
  df <- read_asv_table(input_path)
  df <- apply_column_aliases(df, config$colunas_alias)
  check_required_columns(df, schema)

  df <- df %>%
    compute_derived_local_columns()

  df
}

BAD_RES_PATTERNS <- paste0(c(
  "Uncultured", "uncultured", "Uncultured archaeon", "Uncultured organism",
  "Uncultured bacterium", "Uncultured Candidatus", "Uncultured prokaryote",
  "Eukaryotic synthetic construct", "16S rRNA amplicon fragment",
  "Invertebrate environmental", "Complete Metagenome-Assembled",
  "PREDICTED: Nomascus", "(Citrus unshiu x Citrus sinensis)"
), collapse = "|")

select_best_blast_hit <- function(df) {
  df %>%
    dplyr::mutate(
      .is_bad_1 = stringr::str_detect(`1_subject header`, BAD_RES_PATTERNS),
      .is_bad_2 = stringr::str_detect(`2_subject header`, BAD_RES_PATTERNS),
      .is_bad_3 = stringr::str_detect(`3_subject header`, BAD_RES_PATTERNS),
      Selected_Hit_Origin = dplyr::case_when(
        !.is_bad_1 ~ "1",
        .is_bad_1 & !is.na(`2_subject header`) & !.is_bad_2 ~ "2",
        .is_bad_1 & .is_bad_2 & !is.na(`3_subject header`) & !.is_bad_3 ~ "3",
        TRUE ~ "None"
      ),
      `BLAST ID` = dplyr::case_when(
        Selected_Hit_Origin == "1" ~ substr(`1_subject header`, 1, 40),
        Selected_Hit_Origin == "2" ~ substr(`2_subject header`, 1, 40),
        Selected_Hit_Origin == "3" ~ substr(`3_subject header`, 1, 40),
        TRUE ~ "Match_not_reliable"
      ),
      query_taxID = dplyr::case_when(
        Selected_Hit_Origin == "1" ~ `1_staxid`,
        Selected_Hit_Origin == "2" ~ `2_staxid`,
        Selected_Hit_Origin == "3" ~ `3_staxid`,
        TRUE ~ NA_real_
      ),
      # Segunda passada: pega "ncultur"/"nvironmental" que tenha escapado do
      # primeiro filtro (ex. no meio do header, nao so no comeco).
      `BLAST ID` = dplyr::if_else(
        stringr::str_detect(`BLAST ID`, "ncultur|nvironmental"),
        "Match_not_reliable", `BLAST ID`
      )
    ) %>%
    dplyr::select(-.is_bad_1, -.is_bad_2, -.is_bad_3)
}

clean_species_names <- function(df) {
  df %>%
    dplyr::mutate(`BLAST ID` = dplyr::case_when(
      `BLAST ID` == "Match_not_reliable" ~ "Match_not_reliable",
      TRUE ~ `BLAST ID` %>%
        stringr::str_replace_all(c(
          "Uncultured" = "", "uncultured" = "", "Candidatus" = "",
          "MAG:" = "", "MAG TPA_asm:" = "", "TPA_asm:" = "",
          "^Cf\\. " = "", "^cf\\. " = "", "candidate division" = "",
          "\\[" = "", "\\]" = "", "'" = "", "," = "", "\\n" = ""
        )) %>%
        stringr::str_replace_all(c(" cf. " = " ", " nr. " = " ")) %>%
        stringr::str_squish() %>%
        stringr::word(1, 2, sep = stringr::fixed(" "))
    )) %>%
    dplyr::mutate(`BLAST ID` = dplyr::recode(`BLAST ID`,
      "Human DNA" = "Homo sapiens",
      "Human chromosome" = "Homo sapiens",
      "Eukaryotic synthetic" = "Homo sapiens"
    ))
}

run_blast_refinement <- function(df) {
  cat("\n[2/6] Refinamento dos hits de BLAST: escolhendo o melhor hit por sequencia...\n")
  df <- df %>%
    select_best_blast_hit() %>%
    clean_species_names()
  diag_add("refinamento_hits", list(
    selected_hit_origin = format_counts(df$Selected_Hit_Origin),
    match_not_reliable_count = sum(df$`BLAST ID` == "Match_not_reliable"),
    total_linhas = nrow(df)
  ))
  df
}

TAXONOMY_RANKS <- c(
  "Superkingdom (NCBI)", "Kingdom (NCBI)", "Phylum (NCBI)",
  "Subphylum (NCBI)", "Class (NCBI)", "Subclass (NCBI)",
  "Order (NCBI)", "Suborder (NCBI)", "Family (NCBI)",
  "Subfamily (NCBI)", "Genus (NCBI)"
)

retrieve_taxonomy <- function(taxids, entrez_key = NULL, batch_size = 50, verbose = TRUE) {
  taxids <- unique(taxids[!is.na(taxids)])
  rank_rename <- c(
    superkingdom = "Superkingdom (NCBI)", kingdom = "Kingdom (NCBI)",
    phylum = "Phylum (NCBI)", subphylum = "Subphylum (NCBI)",
    class = "Class (NCBI)", subclass = "Subclass (NCBI)",
    order = "Order (NCBI)", suborder = "Suborder (NCBI)",
    family = "Family (NCBI)", subfamily = "Subfamily (NCBI)",
    genus = "Genus (NCBI)", species = "Sci_name"
  )

  if (length(taxids) == 0) {
    empty <- tibble::tibble(query_taxID = numeric(0))
    for (col in unique(rank_rename)) empty[[col]] <- character(0)
    return(empty)
  }

  if (!is.null(entrez_key) && nzchar(entrez_key)) {
    Sys.setenv(ENTREZ_KEY = entrez_key)
  }

  if (verbose) message(sprintf("Consultando taxonomia de %d taxid(s) unico(s) no NCBI...", length(taxids)))

  uid_batches <- split(taxids, ceiling(seq_along(taxids) / batch_size))
  cls_list <- list()

  for (i in seq_along(uid_batches)) {
    if (verbose) message(sprintf("Lote %d de %d...", i, length(uid_batches)))
    batch_res <- tryCatch(
      taxize::classification(uid_batches[[i]], db = "ncbi", return_id = TRUE),
      error = function(e) {
        warning(sprintf("Falha no lote %d: %s", i, conditionMessage(e)), call. = FALSE)
        NULL
      }
    )
    if (!is.null(batch_res)) cls_list <- c(cls_list, batch_res)
    Sys.sleep(if (!is.null(entrez_key) && nzchar(entrez_key)) 0.15 else 0.4)
  }

  tax_long <- purrr::imap_dfr(cls_list, function(cls_df, curr_uid) {
    if (!is.data.frame(cls_df) || nrow(cls_df) == 0) {
      return(tibble::tibble(query_taxID = as.numeric(curr_uid), rank = NA_character_, name = NA_character_))
    }
    cls_df %>%
      dplyr::select(rank, name) %>%
      dplyr::mutate(query_taxID = as.numeric(curr_uid))
  })

  # Ranks "ruido" que o taxize as vezes devolve e que sao descartados
  # explicitamente por nao serem informativos.
  noise_ranks <- c(
    "cellular root", "clade", "domain", "no rank", "subtribe", "forma",
    "subspecies", "subgenus", "section", "varietas", "genotype", "NA"
  )

  wide_tax <- tax_long %>%
    dplyr::filter(!is.na(rank), !rank %in% noise_ranks) %>%
    dplyr::distinct(query_taxID, rank, .keep_all = TRUE) %>%
    tidyr::pivot_wider(id_cols = query_taxID, names_from = rank, values_from = name, values_fill = NA_character_)

  present_renames <- rank_rename[names(rank_rename) %in% colnames(wide_tax)]
  if (length(present_renames) > 0) {
    wide_tax <- dplyr::rename(wide_tax, !!!setNames(names(present_renames), present_renames))
  }

  for (col in setdiff(unique(rank_rename), colnames(wide_tax))) wide_tax[[col]] <- NA_character_

  if (verbose) message("Taxonomia recuperada.")
  wide_tax
}

fill_missing_ranks <- function(taxonomy_tbl) {
  taxonomy_tbl <- taxonomy_tbl %>%
    dplyr::mutate(
      `Genus (NCBI)` = dplyr::case_when(
        is.na(`Genus (NCBI)`) & !is.na(Sci_name) ~ stringr::word(Sci_name, 1),
        TRUE ~ `Genus (NCBI)`
      ),
      `Superkingdom (NCBI)` = dplyr::case_when(
        !is.na(`Superkingdom (NCBI)`) ~ `Superkingdom (NCBI)`,
        `Kingdom (NCBI)` %in% c("Promethearchaeati", "Nanobdellati", "Methanobacteriati", "Thermoproteati") ~ "Archaea",
        `Kingdom (NCBI)` %in% c("Viridiplantae", "Metazoa", "Fungi") ~ "Eukaryota",
        `Phylum (NCBI)` %in% c(
          "Ciliophora", "Euglenozoa", "Oomycota", "Bacillariophyta", "Tubulinea",
          "Apicomplexa", "Cercozoa", "Endomyxa", "Haptophyta", "Hemimastigophora",
          "Preaxostyla", "Fornicata", "Rhodophyta", "Discosea", "Heterolobosea",
          "Nibbleridia", "Parabasalia", "Perkinsozoa", "Foraminifera", "Telonemia",
          "Nebulidia", "Evosea"
        ) ~ "Eukaryota",
        `Kingdom (NCBI)` %in% c("Pseudomonadati", "Bacillati", "Fusobacteriati", "Thermotogati") ~ "Bacteria",
        stringr::str_detect(`Phylum (NCBI)`, "bacteriota$|bacterota$|bacteria$") ~ "Bacteria",
        stringr::str_detect(
          `Phylum (NCBI)`,
          "Altimarinota$|Moduliflexota$|Microgenomatota$|Binatota$|CPR2$|CPR3$|Methylomirabilota$|Sumerlaeota$"
        ) ~ "Bacteria",
        `Kingdom (NCBI)` %in% c("Heunggongvirae") ~ "Viruses",
        stringr::str_detect(`Phylum (NCBI)`, "WWE3$") ~ "Viruses",
        TRUE ~ `Superkingdom (NCBI)`
      )
    ) %>%
    dplyr::mutate(dplyr::across(
      dplyr::all_of(TAXONOMY_RANKS),
      ~ dplyr::na_if(., "") %>% dplyr::na_if("NA")
    ))

  for (i in 2:length(TAXONOMY_RANKS)) {
    current_rank <- TAXONOMY_RANKS[i]
    higher_rank <- TAXONOMY_RANKS[i - 1]
    rank_prefix <- stringr::str_to_lower(stringr::word(current_rank, 1))
    taxonomy_tbl <- taxonomy_tbl %>%
      dplyr::mutate(!!rlang::sym(current_rank) := dplyr::coalesce(
        !!rlang::sym(current_rank), paste0(rank_prefix, " of ", !!rlang::sym(higher_rank))
      ))
  }

  taxonomy_tbl %>%
    dplyr::mutate(`Superkingdom (NCBI)` = dplyr::coalesce(
      `Superkingdom (NCBI)`, paste0("superkingdom of ", `Kingdom (NCBI)`)
    ))
}

join_taxonomy_and_finalize <- function(df, taxonomy_tbl) {
  df %>%
    dplyr::left_join(taxonomy_tbl, by = "query_taxID") %>%
    dplyr::mutate(`BLAST ID` = dplyr::if_else(
      stringr::str_detect(`BLAST ID`, "^[A-Za-z]\\.[^ ]+ [^ ]+$"),
      stringr::str_c(`Genus (NCBI)`, stringr::str_extract(`BLAST ID`, "(?<=\\.)[^ ]+"), sep = " "),
      `BLAST ID`
    )) %>%
    dplyr::select(-dplyr::any_of("Sci_name"))
}

run_taxonomy_lookup <- function(df, entrez_key = NULL) {
  taxonomy_tbl <- df$query_taxID %>%
    retrieve_taxonomy(entrez_key = entrez_key) %>%
    fill_missing_ranks()

  join_taxonomy_and_finalize(df, taxonomy_tbl)
}

CONTROL_TYPE_VALUES <- c(
  "Ext. Control", "Ext. control", "Extraction Control", "Extraction control",
  "Filt. Control", "Filt. control", "Filtration Control", "Filtration control",
  "PCR Control", "PCR control",
  "Negative Control", "Negative control", "Positive Control", "Positive control",
  "Control"
)

CONTROL_REFERENCE_COLUMNS <- c(
  "Ext. Control"  = "FC to Ext control",
  "PCR Control"   = "FC to PCR control",
  "Filt. Control" = "FC to Filt control"
)

foldchange <- function(num, denom) {
  ifelse(num >= denom, num / denom, -denom / num)
}

safe_max <- function(x) {
  if (all(is.na(x))) NA_real_ else max(x, na.rm = TRUE)
}

flag_contamination <- function(df, fold_change_threshold = 10) {
  df <- df %>%
    dplyr::mutate(`Relative Abundance Sample` = dplyr::if_else(
      `Sample total abundance` > 0,
      `ASV absolute abundance` / `Sample total abundance`,
      0
    ))

  ctrl_cols <- intersect(names(CONTROL_REFERENCE_COLUMNS), colnames(df))
  if (length(ctrl_cols) == 0) {
    warning(
      "Nenhuma coluna de controle (Ext./PCR/Filt. Control) encontrada -- ",
      "pulando checagem de contaminacao.",
      call. = FALSE
    )
    return(dplyr::mutate(df, `Contamination status` = NA_character_, `Control presence` = NA))
  }

  contam_lookup <- df %>%
    dplyr::filter(`ASV absolute abundance` > 0, Type %in% CONTROL_TYPE_VALUES) %>%
    dplyr::group_by(Primer, Unique_File_name, `ASV (Sequence)`) %>%
    dplyr::summarise(Max_ctrl_abd = safe_max(`Relative Abundance Sample`), .groups = "drop")

  for (ctrl_col in ctrl_cols) {
    target_col <- CONTROL_REFERENCE_COLUMNS[[ctrl_col]]

    ctrl_matched <- df %>%
      dplyr::filter(!Type %in% CONTROL_TYPE_VALUES, !is.na(.data[[ctrl_col]]), .data[[ctrl_col]] != "") %>%
      dplyr::mutate(.ctrl_ref = stringr::str_trim(.data[[ctrl_col]])) %>%
      dplyr::inner_join(
        contam_lookup,
        by = c("Primer", ".ctrl_ref" = "Unique_File_name", "ASV (Sequence)")
      ) %>%
      dplyr::group_by(Primer, Unique_File_name, `ASV (Sequence)`) %>%
      dplyr::summarise(Max_ctrl_abd = safe_max(Max_ctrl_abd), .groups = "drop")

    df <- df %>%
      dplyr::left_join(ctrl_matched, by = c("Primer", "Unique_File_name", "ASV (Sequence)")) %>%
      dplyr::mutate(!!target_col := dplyr::if_else(
        !is.na(Max_ctrl_abd),
        foldchange(`Relative Abundance Sample`, Max_ctrl_abd),
        NA_real_
      )) %>%
      dplyr::select(-Max_ctrl_abd)
  }

  fc_cols <- unname(CONTROL_REFERENCE_COLUMNS[ctrl_cols])
  fc_matrix <- as.matrix(df[fc_cols])
  contam_flag <- apply(fc_matrix, 1, function(row) any(!is.na(row) & row < fold_change_threshold))
  control_presence_flag <- apply(fc_matrix, 1, function(row) any(!is.na(row)))

  df %>%
    dplyr::mutate(
      `Control presence` = control_presence_flag,
      `Contamination status` = dplyr::if_else(contam_flag, "Possible contamination", "True detection")
    )
}

run_taxonomy_and_contamination <- function(df, config = DEFAULT_CONFIG, entrez_key = NULL) {
  cat("\n[3/6] Taxonomia NCBI + contaminacao: consultando taxonomia e calculando Fold Change...\n")
  df <- run_taxonomy_lookup(df, entrez_key = entrez_key)
  df <- flag_contamination(df, fold_change_threshold = config$contaminacao$fold_change_threshold)
  diag_add("taxonomia_contaminacao", list(
    contamination_status = format_counts(df$`Contamination status`)
  ))
  df
}

resolve_amplicon_ranges <- function(df, amplicon_por_primer) {
  resolved <- amplicon_por_primer
  for (primer in unique(df$Primer)) {
    if (!is.null(resolved[[primer]])) next

    sizes <- df$`ASV Size (pb)`[df$Primer == primer]
    sizes <- sizes[!is.na(sizes)]
    if (length(sizes) < 4) {
      warning(sprintf(
        "Primer '%s' sem faixa declarada em amplicon_por_primer e com poucos dados (%d ASVs) pra estimar automaticamente -- Primer expected length fica indefinido para ele.",
        primer, length(sizes)
      ), call. = FALSE)
      next
    }

    q <- stats::quantile(sizes, probs = c(0.25, 0.75), names = FALSE)
    iqr <- q[2] - q[1]
    lower <- max(0, q[1] - 1.5 * iqr)
    upper <- q[2] + 1.5 * iqr
    resolved[[primer]] <- c(lower, upper)
    message(sprintf(
      "Faixa de amplicon do primer '%s' detectada automaticamente: %.0f-%.0f pb (%d ASVs, IQR).",
      primer, lower, upper, length(sizes)
    ))
  }
  resolved
}

flag_amplicon_length <- function(df, amplicon_por_primer) {
  range_lookup <- function(primer) amplicon_por_primer[[primer]]

  df %>%
    dplyr::rowwise() %>%
    dplyr::mutate(`Primer expected length` = {
      rng <- range_lookup(Primer)
      if (is.null(rng)) {
        NA_character_
      } else if (`ASV Size (pb)` >= rng[[1]] && `ASV Size (pb)` <= rng[[2]]) {
        "in range"
      } else {
        "out of range"
      }
    }) %>%
    dplyr::ungroup()
}

compute_pseudoscore_and_identification <- function(df, thresholds) {
  df <- df %>%
    dplyr::mutate(
      .sel_identity = dplyr::case_when(
        Selected_Hit_Origin == "1" ~ `1_indentity`,
        Selected_Hit_Origin == "2" ~ `2_indentity`,
        Selected_Hit_Origin == "3" ~ `3_indentity`,
        TRUE ~ NA_real_
      ),
      .sel_qcovhsp = dplyr::case_when(
        Selected_Hit_Origin == "1" ~ `1_qcovhsp`,
        Selected_Hit_Origin == "2" ~ `2_qcovhsp`,
        Selected_Hit_Origin == "3" ~ `3_qcovhsp`,
        TRUE ~ NA_real_
      ),
      `BLASTn pseudo-score` = (10 * .sel_identity + .sel_qcovhsp) / 11
    )

  df %>%
    dplyr::mutate(
      Identification = dplyr::case_when(
        `BLASTn pseudo-score` >= thresholds$especie ~ `BLAST ID`,
        # "Genero sp." (nomenclatura binomial), nao so o genero sozinho --
        # mantem o padrao com o resto do sistema, que sempre trabalha com
        # a convencao binomial mesmo quando a identificacao nao chega a
        # nivel de especie.
        `BLASTn pseudo-score` >= thresholds$genero & !is.na(`Genus (NCBI)`) ~ paste0(`Genus (NCBI)`, " sp."),
        `BLASTn pseudo-score` >= thresholds$genero ~ NA_character_,
        `BLASTn pseudo-score` >= thresholds$familia ~ `Family (NCBI)`,
        `BLASTn pseudo-score` >= thresholds$ordem ~ `Order (NCBI)`,
        `BLASTn pseudo-score` >= thresholds$classe ~ `Class (NCBI)`,
        TRUE ~ "Unidentified"
      ),
      `Identification Max. taxonomy` = dplyr::case_when(
        `BLASTn pseudo-score` >= thresholds$especie ~ "Species",
        `BLASTn pseudo-score` >= thresholds$genero ~ "Genus",
        `BLASTn pseudo-score` >= thresholds$familia ~ "Family",
        `BLASTn pseudo-score` >= thresholds$ordem ~ "Order",
        `BLASTn pseudo-score` >= thresholds$classe ~ "Class",
        TRUE ~ "Unidentified"
      )
    ) %>%
    dplyr::select(-.sel_identity, -.sel_qcovhsp)
}

TARGET_TAXA_REGISTRY <- list(
  Metazoa = list(
    kingdom = "Metazoa",
    phyla = character(0),
    classes = character(0)
  ),
  # Restrito a peixes ossseos (classe Actinopteri), nao ao reino animal
  # inteiro -- "Metazoa" sozinho deixa passar qualquer animal (ex.
  # contaminacao de Bos taurus, comum em reagente de laboratorio) como
  # "Possible target taxon = TRUE" num projeto de eDNA de peixes, gastando
  # cota de LLM revisando identificacao de organismo fora do escopo real.
  # Mesma classe usada no filtro do notebook de referencia original
  # (scripts/ecology/reference/eDNA_cipo_script.qmd: Class (NCBI) ==
  # "Actinopteri").
  Actinopteri = list(
    kingdom = NA_character_,
    phyla = character(0),
    classes = c("Actinopteri")
  ),
  Benthos = list(
    kingdom = NA_character_,
    phyla = c("Annelida", "Arthropoda", "Mollusca"),
    classes = c("Bivalvia", "Clitellata", "Gastropoda", "Insecta")
  ),
  Zooplankton = list(
    kingdom = NA_character_,
    phyla = c("Amoebozoa", "Cercozoa", "Ciliophora", "Rotifera"),
    classes = c("Branchiopoda", "Eurotatoria", "Filosia", "Hexanauplia", "Lobosa", "Oligohymenophorea")
  ),
  Periphyton = list(
    kingdom = NA_character_,
    phyla = c(
      "Bacillariophyta", "Charophyta", "Chlorophyta", "Cryptophyta",
      "Cyanobacteria", "Euglenophyta", "Rhodophyta"
    ),
    classes = c(
      "Bacillariophyceae", "Chlorophyceae", "Chrysophyceae",
      "Coscinodiscophyceae", "Cryptophyceae", "Cyanophyceae",
      "Euglenophyceae", "Florideophyceae", "Mediophyceae", "Ulvophyceae",
      "Zygnematophyceae"
    )
  ),
  Phytoplankton = list(
    kingdom = NA_character_,
    phyla = c(
      "Dinophyta", "Ochrophyta", "Apicomplexa", "Discosea", "Endomyxa",
      "Euglenozoa", "Evosea", "Foraminifera", "Fornicata", "Haptophyta",
      "Hemimastigophora", "Heterolobosea", "Nebulidia", "Nibbleridia",
      "Oomycota", "Parabasalia", "Perkinsozoa", "Preaxostyla", "Tubulinea"
    ),
    classes = c(
      "Dinophyceae", "Trebouxiophyceae", "Actinophryidae", "Aphelidea",
      "Bigyra", "Bolidophyceae", "Breviatea", "Centroplasthelida",
      "Choanoflagellata", "Developea", "Dictyochophyceae",
      "Eustigmatophyceae", "Filasterea", "Glaucocystophyceae",
      "Hyphochytriomycetes", "Ichthyosporea", "Olisthodiscophyceae",
      "Pelagophyceae", "Phaeophyceae", "Phaeothamniophyceae",
      "Raphidophyceae", "Synurophyceae", "Xanthophyceae"
    )
  ),
  # Reino real reportado pelo NCBI pra plantas terrestres e algas verdes e
  # "Viridiplantae", nao "Plantae" -- conferido contra taxid reais de um
  # projeto de eDNA de raizes (marcador ITS2) antes de registrar aqui.
  Plantae = list(
    kingdom = "Viridiplantae",
    phyla = character(0),
    classes = character(0)
  )
)

flag_target_taxa <- function(df, grupos, registry = TARGET_TAXA_REGISTRY) {
  unknown <- setdiff(grupos, names(registry))
  if (length(unknown) > 0) {
    stop(sprintf(
      "Grupo(s) de taxons-alvo desconhecido(s): %s. Grupos disponiveis: %s",
      paste(unknown, collapse = ", "), paste(names(registry), collapse = ", ")
    ), call. = FALSE)
  }

  selected <- registry[grupos]
  target_phyla <- unique(unlist(purrr::map(selected, "phyla")))
  target_classes <- unique(unlist(purrr::map(selected, "classes")))
  target_kingdoms <- unique(purrr::discard(purrr::map_chr(selected, "kingdom"), is.na))

  df %>%
    dplyr::mutate(`Possible target taxon` = dplyr::case_when(
      stringr::str_detect(`BLAST ID`, "Chordate environmental") ~ FALSE,
      stringr::str_detect(`BLAST ID`, "Match_not_reliable") ~ FALSE,
      stringr::str_detect(`Superkingdom (NCBI)`, "Bacteria|Archaea|Viruses") ~ FALSE,
      is.na(`Kingdom (NCBI)`) ~ FALSE,
      stringr::str_detect(`Superkingdom (NCBI)`, "Eukaryota") &
        (`Phylum (NCBI)` %in% target_phyla) ~ TRUE,
      stringr::str_detect(`Superkingdom (NCBI)`, "Eukaryota") &
        (`Class (NCBI)` %in% target_classes) ~ TRUE,
      `Kingdom (NCBI)` %in% target_kingdoms ~ TRUE,
      TRUE ~ FALSE
    ))
}

run_final_curation <- function(df, config = DEFAULT_CONFIG) {
  cat("\n[4/6] Curadoria final: faixa de amplicon, pseudo-score e taxons-alvo...\n")
  df <- df %>%
    flag_amplicon_length(config$amplicon_por_primer) %>%
    compute_pseudoscore_and_identification(config$identificacao$pseudoscore_thresholds) %>%
    flag_target_taxa(config$taxons_alvo$grupos)
  diag_add("curadoria_final", list(
    primer_expected_length = format_counts(df$`Primer expected length`),
    identification_max_taxonomy = format_counts(df$`Identification Max. taxonomy`),
    taxons_alvo_grupos = paste(config$taxons_alvo$grupos, collapse = ", "),
    possible_target_taxon = format_counts(df$`Possible target taxon`)
  ))
  df
}

dnastringset_to_dnabin <- function(dna_stringset) {
  char_list <- as.character(dna_stringset)
  mat <- do.call(rbind, base::strsplit(char_list, ""))
  rownames(mat) <- names(dna_stringset)
  ape::as.DNAbin(mat)
}

build_asv_tree <- function(df, amplicon_por_primer) {
  unique_asvs <- df %>%
    dplyr::distinct(Primer, `ASV (Sequence)`, `ASV header`) %>%
    dplyr::rowwise() %>%
    dplyr::mutate(.floor_bp = {
      rng <- amplicon_por_primer[[Primer]]
      if (is.null(rng)) NA_real_ else rng[[1]]
    }) %>%
    dplyr::ungroup() %>%
    dplyr::filter(!is.na(.floor_bp), nchar(`ASV (Sequence)`) >= .floor_bp)

  if (nrow(unique_asvs) < 3) {
    warning(
      "Menos de 3 ASVs unicas passam no piso de tamanho do primer -- ",
      "arvore filogenetica pulada (vizinhos ficam NA).",
      call. = FALSE
    )
    return(NULL)
  }

  dna_set <- Biostrings::DNAStringSet(unique_asvs$`ASV (Sequence)`)
  names(dna_set) <- unique_asvs$`ASV header`

  aligned <- DECIPHER::AlignSeqs(dna_set, anchor = NA, verbose = FALSE)
  dna_bin <- dnastringset_to_dnabin(aligned)

  # njs() (nao nj()) porque pairwise.deletion pode gerar NA quando duas ASVs
  # nao tem nenhum sitio comparavel em comum no alinhamento -- njs() e a
  # variante do Neighbor-Joining tolerante a distancias faltantes.
  dist_matrix <- ape::dist.dna(dna_bin, model = "raw", pairwise.deletion = TRUE)
  ape::njs(dist_matrix)
}

extract_k_neighbors <- function(tree, k = 5) {
  if (is.null(tree)) {
    return(tibble::tibble(`ASV header` = character(0), `Vizinhos filogeneticos (k)` = character(0)))
  }

  patristic <- ape::cophenetic.phylo(tree)

  neighbor_strings <- vapply(rownames(patristic), function(tip) {
    dists <- sort(patristic[tip, ])
    dists <- dists[names(dists) != tip]
    top_k <- names(dists)[seq_len(min(k, length(dists)))]
    paste(top_k, collapse = "; ")
  }, character(1))

  tibble::tibble(
    `ASV header` = rownames(patristic),
    `Vizinhos filogeneticos (k)` = neighbor_strings
  )
}

run_phylogenetic_tree <- function(df, config = DEFAULT_CONFIG) {
  cat("\n[5/6] Arvore filogenetica: alinhando sequencias e calculando vizinhos mais proximos...\n")
  tree <- build_asv_tree(df, config$amplicon_por_primer)
  neighbors <- extract_k_neighbors(tree, k = config$arvore_filogenetica$k_vizinhos)

  df <- df %>%
    dplyr::left_join(neighbors, by = "ASV header")
  diag_add("arvore_filogenetica", list(
    vizinhos_calculados = sum(!is.na(df$`Vizinhos filogeneticos (k)`)),
    total_linhas = nrow(df)
  ))
  df
}

compute_area_bbox <- function(df, config) {
  if (!is.null(config$checagem_regional$area_bbox)) {
    return(config$checagem_regional$area_bbox)
  }
  if (!all(c("Latitude", "Longitude") %in% colnames(df))) {
    return(NULL)
  }

  lat <- df$Latitude[!is.na(df$Latitude)]
  long <- df$Longitude[!is.na(df$Longitude)]
  if (length(lat) == 0 || length(long) == 0) {
    return(NULL)
  }

  buffer <- config$checagem_regional$buffer_graus
  list(
    lat_min = min(lat) - buffer, lat_max = max(lat) + buffer,
    long_min = min(long) - buffer, long_max = max(long) + buffer
  )
}

bbox_to_wkt <- function(area_bbox) {
  sprintf(
    "POLYGON((%f %f, %f %f, %f %f, %f %f, %f %f))",
    area_bbox$long_min, area_bbox$lat_min,
    area_bbox$long_max, area_bbox$lat_min,
    area_bbox$long_max, area_bbox$lat_max,
    area_bbox$long_min, area_bbox$lat_max,
    area_bbox$long_min, area_bbox$lat_min
  )
}

gbif_regional_count <- function(scientific_name, wkt, verbose = TRUE, max_retries = 4) {
  if (is.na(scientific_name) || !nzchar(scientific_name)) return(NA_integer_)
  if (verbose) message(sprintf("GBIF: %s", scientific_name))

  for (attempt in seq_len(max_retries)) {
    result <- tryCatch(
      as.integer(rgbif::occ_count(scientificName = scientific_name, geometry = wkt)),
      error = function(e) e
    )
    # Throttle entre chamadas consecutivas ao GBIF, mesmo padrao usado na
    # consulta de taxonomia do NCBI (retrieve_taxonomy).
    Sys.sleep(0.3)

    if (!inherits(result, "error")) return(result)

    rate_limited <- grepl("too many requests", conditionMessage(result), ignore.case = TRUE)
    if (!rate_limited) {
      warning(
        sprintf("Falha na consulta GBIF para '%s': %s", scientific_name, conditionMessage(result)),
        call. = FALSE
      )
      return(NA_integer_)
    }

    if (attempt < max_retries) {
      if (verbose) {
        message(sprintf("GBIF: limite de taxa (tentativa %d/%d), aguardando...", attempt, max_retries))
      }
      Sys.sleep(2^attempt)
    }
  }

  warning(
    sprintf(
      "Falha na consulta GBIF para '%s': limite de taxa persistente apos %d tentativas",
      scientific_name, max_retries
    ),
    call. = FALSE
  )
  NA_integer_
}

run_regional_check <- function(df, config = DEFAULT_CONFIG) {
  cat("\n[6/6] Checagem regional GBIF: consultando ocorrencia por especie...\n")
  if (!identical(config$checagem_regional$fonte, "gbif")) {
    warning(
      "checagem_regional$fonte != 'gbif' -- checagem regional pulada ",
      "(sem implementacao alternativa ainda).",
      call. = FALSE
    )
    df <- dplyr::mutate(df, `GBIF regional occurrence count` = NA_integer_)
    diag_add("checagem_regional", list(
      gbif_consultadas = 0L, pulada = TRUE, motivo = "checagem_regional$fonte != 'gbif'"
    ))
    return(df)
  }

  area_bbox <- compute_area_bbox(df, config)
  if (is.null(area_bbox)) {
    warning(
      "Sem area_bbox declarada e sem Latitude/Longitude nos dados -- ",
      "checagem regional pulada.",
      call. = FALSE
    )
    df <- dplyr::mutate(df, `GBIF regional occurrence count` = NA_integer_)
    diag_add("checagem_regional", list(
      gbif_consultadas = 0L, pulada = TRUE,
      motivo = "sem area_bbox declarada e sem Latitude/Longitude nos dados"
    ))
    return(df)
  }

  wkt <- bbox_to_wkt(area_bbox)

  species_lookup <- df %>%
    dplyr::filter(`Identification Max. taxonomy` == "Species") %>%
    dplyr::distinct(Identification) %>%
    dplyr::mutate(
      `GBIF regional occurrence count` = purrr::map_int(Identification, gbif_regional_count, wkt = wkt)
    )

  df <- df %>%
    dplyr::left_join(species_lookup, by = "Identification")
  diag_add("checagem_regional", list(
    gbif_consultadas = sum(!is.na(df$`GBIF regional occurrence count`)), pulada = FALSE
  ))
  df
}

ORIGINAL_LONG_COLUMN_ORDER <- c(
  "Researcher", "Project", "BLASTn pseudo-score", "Identification",
  "Identification Max. taxonomy", "Primer", "Sample", "Unique_File_name",
  "Total clean sample abd.", "Clean relative abd. on sample",
  "Relative abundance to all samples", "Relative abundance on sample",
  "Sample total abundance", "ASV absolute abundance", "Metadata 1",
  "Metadata 2", "Metadata 3", "Metadata 4", "Metadata 5", "Metadata 6",
  "Metadata 7", "Metadata 8", "Metadata 9", "Metadata 10", "Metadata 11",
  "Metadata 12", "obs", "Primer expected length", "ASV Size (pb)",
  "Possible Metazoa", "Curated ID", "Final ID (BLASTn)", "blast ID Origin",
  "ID status", "Contamination status", "ASV clean abs. abd.", "BLAST ID",
  "Genus (NCBI)", "Subfamily (NCBI)", "Family (NCBI)", "Suborder (NCBI)",
  "Order (NCBI)", "Subclass (NCBI)", "Class (NCBI)", "Phylum (NCBI)",
  "Subphylum (NCBI)", "Kingdom (NCBI)", "Superkingdom (NCBI)",
  "1_subject header", "1_staxid", "1_subject", "1_indentity", "1_qcovhsp",
  "1_length", "1_mismatches", "1_gaps", "1_query start", "1_query end",
  "1_subject start", "1_subject end", "1_e-value", "1_bitscore",
  "2_subject header", "2_staxid", "2_subject", "2_indentity", "2_qcovhsp",
  "2_length", "2_mismatches", "2_gaps", "2_query start", "2_query end",
  "2_subject start", "2_subject end", "2_e-value", "2_bitscore",
  "3_subject header", "3_staxid", "3_subject", "3_indentity", "3_qcovhsp",
  "3_length", "3_mismatches", "3_gaps", "3_query start", "3_query end",
  "3_subject start", "3_subject end", "3_e-value", "3_bitscore",
  "ASV header", "ASV (Sequence)", "Sequence (ASV tip)", "OTU",
  "Ext. Control", "PCR Control", "Filt. Control", "Prop. to PCR control",
  "Prop. to Ext control", "Prop. to Filt control", "Type"
)

COLUMN_EQUIVALENTS <- c(
  "Possible target taxon" = "Possible Metazoa",
  "Ponto" = "Metadata 1",
  "Latitude" = "Metadata 8",
  "Longitude" = "Metadata 9",
  "Habitat" = "Metadata 10",
  "Rios" = "Metadata 11"
)

NEW_OUTPUT_COLUMNS <- c(
  "Selected_Hit_Origin", "FC to Ext control", "FC to Filt control",
  "Control presence", "Vizinhos filogeneticos (k)",
  "GBIF regional occurrence count"
)

finalize_output_columns <- function(df, original_order = ORIGINAL_LONG_COLUMN_ORDER,
                                     equivalents = COLUMN_EQUIVALENTS,
                                     new_columns = NEW_OUTPUT_COLUMNS) {
  reverse_equivalents <- setNames(names(equivalents), equivalents)

  ordered_cols <- purrr::map_chr(original_order, function(original_name) {
    # `[` (nao `[[`) num vetor nomeado atomico devolve NA pra chave
    # ausente em vez de dar erro -- reverse_equivalents e um vetor, nao
    # uma lista.
    our_name <- unname(reverse_equivalents[original_name])
    if (!is.na(our_name) && our_name %in% colnames(df)) return(our_name)
    if (original_name %in% colnames(df)) return(original_name)
    NA_character_
  })
  ordered_cols <- ordered_cols[!is.na(ordered_cols)]

  new_cols_present <- intersect(new_columns, colnames(df))

  dplyr::select(df, dplyr::all_of(c(ordered_cols, new_cols_present)))
}

parse_cli_args <- function(args) {
  config_path <- NULL
  output_path <- NULL
  positional <- character(0)

  for (a in args) {
    if (startsWith(a, "--config=")) {
      config_path <- sub("^--config=", "", a)
    } else if (startsWith(a, "--output=")) {
      output_path <- sub("^--output=", "", a)
    } else {
      positional <- c(positional, a)
    }
  }

  if (length(positional) < 1) {
    stop(
      "Uso: Rscript curadoria_deterministica.R <entrada.csv> [--config=config.yaml] [--output=output_pos_curadoria_LLM-AAAA-MM-DD.csv]",
      call. = FALSE
    )
  }

  list(input = positional[1], config = config_path, output = output_path)
}

main <- function(args) {
  parsed <- parse_cli_args(args)
  config <- load_config(parsed$config)

  df <- run_setup_parsing(parsed$input, config = config)
  config$amplicon_por_primer <- resolve_amplicon_ranges(df, config$amplicon_por_primer)
  df <- run_blast_refinement(df)
  df <- run_taxonomy_and_contamination(df, config = config)
  df <- run_final_curation(df, config = config)
  df <- run_phylogenetic_tree(df, config = config)
  df <- run_regional_check(df, config = config)
  df <- finalize_output_columns(df)

  etapas <- pipeline_diagnostics$etapas

  cat(sprintf("Tabela parseada: %d linhas, %d colunas.\n", nrow(df), ncol(df)))
  cat("Colunas calculadas localmente: ASV Size (pb), Sample total abundance, ASV header.\n")
  cat(sprintf("Origem do hit selecionado (Selected_Hit_Origin): %s\n", etapas$refinamento_hits$selected_hit_origin))
  cat(sprintf(
    "BLAST ID = 'Match_not_reliable' em %d de %d linhas.\n",
    etapas$refinamento_hits$match_not_reliable_count, etapas$refinamento_hits$total_linhas
  ))
  cat(sprintf("Status de contaminacao: %s\n", etapas$taxonomia_contaminacao$contamination_status))
  cat(sprintf("Faixa de amplicon (Primer expected length): %s\n", etapas$curadoria_final$primer_expected_length))
  cat(sprintf("Identificacao maxima (Identification Max. taxonomy): %s\n", etapas$curadoria_final$identification_max_taxonomy))
  cat(sprintf("Grupos de taxons-alvo configurados: %s\n", etapas$curadoria_final$taxons_alvo_grupos))
  cat(sprintf("Possible target taxon: %s\n", etapas$curadoria_final$possible_target_taxon))
  cat(sprintf(
    "Vizinhos filogeneticos calculados para %d de %d linhas (NA = ASV abaixo do piso de tamanho).\n",
    etapas$arvore_filogenetica$vizinhos_calculados, etapas$arvore_filogenetica$total_linhas
  ))
  cat(sprintf(
    "Checagem regional GBIF: %d linhas com identificacao em nivel de especie consultadas.\n",
    etapas$checagem_regional$gbif_consultadas
  ))

  if (!is.null(parsed$output)) {
    readr::write_csv2(df, parsed$output)
    cat(sprintf("Tabela parseada salva em: %s\n", parsed$output))

    diagnostics_path <- sub("\\.csv$", "_diagnostics.json", parsed$output)
    jsonlite::write_json(etapas, diagnostics_path, auto_unbox = TRUE, pretty = TRUE, na = "null")
    cat(sprintf("Diagnosticos estruturados salvos em: %s\n", diagnostics_path))
  }

  invisible(df)
}

# CURADORIA_TESTING=true permite aos testes (r/tests/testthat) fazer source()
# deste arquivo so pra reusar as definicoes de funcao, sem disparar main()
# (que espera <entrada.csv> via linha de comando e daria stop()).
if (!interactive() && !identical(Sys.getenv("CURADORIA_TESTING"), "true")) {
  main(commandArgs(trailingOnly = TRUE))
}
