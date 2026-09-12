# curadoria_deterministica.R
#
# Curadoria taxonomica deterministica para a tabela de ASVs (eDNA_Cipo,
# MiFish2). Nenhuma etapa aqui depende de interpretacao de um LLM -- tudo
# reproduzivel a partir dos parametros fechados nas notas de arquitetura
# (claude_notas-arquitetura-desafio-amplo-parte2.md).
#
# Escrito em blocos, para revisao incremental:
#   [x] Bloco 1 - setup / parsing
#   [x] Bloco 2 - refinamento de hits BLAST + limpeza de nome
#   [x] Bloco 3 - taxonomia NCBI + contaminacao
#   [x] Bloco 4 - curadoria final (faixas / Metazoa / pseudo-score / flags) (este arquivo, por enquanto)
#   [ ] Bloco 5 - arvore filogenetica + extracao de vizinhos
#   [ ] Bloco 6 - checagem regional GBIF + exportacao
#
# Uso via linha de comando:
#   Rscript curadoria_deterministica.R <entrada.csv> [--config=config.yaml] [--output=saida.csv]
#
# Uso interativo (RStudio/Positron): abra este arquivo, rode `source()` (ou
# execute por secoes com Ctrl+Enter) -- o `if (!interactive())` no fim
# impede que main() rode sozinho, entao as funcoes abaixo ficam disponiveis
# para chamar uma a uma e inspecionar o resultado no Data Explorer.

library(tidyverse)
library(yaml)
library(taxize)

# ---- Localizacao do repositorio ------------------------------------------

#' Resolve a raiz do repo tanto rodando via `Rscript` quanto de dentro do
#' RStudio/Positron (onde nao ha `--file=` em commandArgs).
get_repo_root <- function() {
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

# ---- Configuracao ----------------------------------------------------------
# Espelha o YAML "fechado" nas notas de arquitetura (parte 2). Qualquer
# override deve vir de --config, nunca editado direto aqui.

DEFAULT_CONFIG <- list(
  contaminacao = list(
    fold_change_threshold = 10
  ),
  amplicon_por_primer = list(
    MiFish2 = c(140, 200)
  ),
  identificacao = list(
    pseudoscore_thresholds = list(
      especie = 98, genero = 95, familia = 90, ordem = 80, classe = 60
    ),
    pseudoscore_hit_base = "selecionado"
  ),
  arvore_filogenetica = list(
    escopo = "ASV-contra-ASV",
    min_length_bp = NULL,
    k_vizinhos = 5
  ),
  checagem_regional = list(
    fonte = "gbif",
    checklist_path = NULL,
    area = "bacia do Sao Francisco + entorno do PARNA/APA (declarada uma vez, nao por ponto)",
    bbox_buffer_km = NULL
  )
)

#' Junta os defaults com um YAML de usuario (merge recursivo via modifyList;
#' so sobrescreve as chaves que o YAML de fato traz).
load_config <- function(config_path = NULL, defaults = DEFAULT_CONFIG) {
  if (is.null(config_path)) return(defaults)
  if (!file.exists(config_path)) {
    stop(sprintf("Arquivo de config '%s' nao encontrado.", config_path), call. = FALSE)
  }
  user_config <- yaml::read_yaml(config_path)
  utils::modifyList(defaults, user_config, keep.null = TRUE)
}

# ---- Leitura e checagem de sanidade -----------------------------------------
# Validacao completa (colunas obrigatorias, vazamento, chave primaria) e
# responsabilidade do harness em Python (tools/schema_validation.py), rodada
# ANTES de chamar este script. A checagem abaixo e so uma guarda leve para
# quando o script roda sozinho (RStudio/Positron) sem passar pelo harness.

#' Le o contrato de schema (mesmo YAML usado pelo validador em Python) para
#' nao duplicar a lista de colunas obrigatorias em duas linguagens.
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

#' Le o CSV bruto do BLASTr: delimitador ";", decimal "," (inclusive em
#' notacao cientifica no e-value, ex. "1,92e-80"), UTF-8, aspas padrao CSV
#' para campos com ";"/quebra de linha interna (ex. controles multiplos).
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

# ---- Parsing de metadados ----------------------------------------------------

#' Renomeia Metadata 1/8/9/10/11 -> Ponto/Latitude/Longitude/Habitat/Rios e
#' corrige Latitude/Longitude, que vem como inteiro (ex. -19420314) em vez de
#' grau decimal -- dividir por 1e6 da o valor real (-19.420314). Sem essa
#' correcao qualquer consulta geografica (GBIF, bloco 6) quebra
#' silenciosamente. Colunas ausentes sao puladas com aviso em vez de erro,
#' ja que Metadata 8-11 sao opcionais no contrato de schema.
rename_metadata_columns <- function(df) {
  rename_map <- c(
    "Metadata 1" = "Ponto",
    "Metadata 8" = "Latitude",
    "Metadata 9" = "Longitude",
    "Metadata 10" = "Habitat",
    "Metadata 11" = "Rios"
  )

  present <- rename_map[names(rename_map) %in% colnames(df)]
  absent <- rename_map[!names(rename_map) %in% colnames(df)]
  if (length(absent) > 0) {
    warning(sprintf(
      "Coluna(s) de metadado ausente(s), renomeacao pulada: %s",
      paste(names(absent), collapse = ", ")
    ), call. = FALSE)
  }

  df <- dplyr::rename(df, !!!setNames(names(present), present))

  if ("Latitude" %in% colnames(df)) {
    df$Latitude <- as.numeric(df$Latitude) / 1e6
  }
  if ("Longitude" %in% colnames(df)) {
    df$Longitude <- as.numeric(df$Longitude) / 1e6
  }
  df
}

# ---- Colunas calculadas localmente -------------------------------------------
# Nao vem no CSV bruto (so existiam na planilha mestre, calculadas por um
# processo externo diferente do nosso) -- recalculadas aqui sempre, nunca
# aceitas vindas do input (ver role: derived_local no contrato de schema).

#' Tamanho do amplicon em pb = numero de bases da sequencia da ASV. Piso de
#' entrada para o filtro de amplicon_por_primer (bloco 4) e para a arvore
#' filogenetica (bloco 5).
add_asv_size <- function(df) {
  dplyr::mutate(df, `ASV Size (pb)` = nchar(`ASV (Sequence)`))
}

#' Abundancia total da amostra = soma de `ASV absolute abundance` de todas as
#' ASVs daquela amostra (mesma amostra repete o mesmo total em cada linha,
#' igual a planilha mestre original).
add_sample_total_abundance <- function(df) {
  dplyr::group_by(df, Sample) %>%
    dplyr::mutate(`Sample total abundance` = sum(`ASV absolute abundance`, na.rm = TRUE)) %>%
    dplyr::ungroup()
}

#' Header no formato ">ASV_{posicao}-{tamanho}bp" (ex. ">ASV_2422-168bp").
#' Posicao = rank da ASV entre as sequencias UNICAS da tabela (a tabela e
#' long-format: a mesma ASV aparece em varias amostras, mas o rank e por
#' sequencia, nao por linha), ordenado por tamanho decrescente; empate de
#' tamanho desempatado pela abundancia total da ASV (soma de
#' `ASV absolute abundance` de todas as amostras onde ela aparece) -- decisao
#' do Gabriel.
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

# ---- Bloco 2: refinamento de hits BLAST + limpeza de nome -------------------
# Porta a logica do roots_metabar_metabarcoding-pipeline.qmd (secoes "Refine
# BLAST Hits" e "Clean Species Names"), por decisao do Gabriel de manter as
# formulas do roots_metabar em vez das do TCC (ver notas de arquitetura).
# Padroes e regras copiados verbatim da fonte; so os nomes de coluna de saida
# foram alinhados ao contrato de schema deste repo (`BLAST ID`, nao
# `blast ID`).

# Nao escapar os parenteses do ultimo padrao é intencional -- na fonte
# original eles tambem nao sao escapados (viram um grupo de regex, nao um
# parenteses literal exigido no texto), entao mantido identico para
# reproduzir o mesmo comportamento de match.
BAD_RES_PATTERNS <- paste0(c(
  "Uncultured", "uncultured", "Uncultured archaeon", "Uncultured organism",
  "Uncultured bacterium", "Uncultured Candidatus", "Uncultured prokaryote",
  "Eukaryotic synthetic construct", "16S rRNA amplicon fragment",
  "Invertebrate environmental", "Complete Metagenome-Assembled",
  "PREDICTED: Nomascus", "(Citrus unshiu x Citrus sinensis)"
), collapse = "|")

#' Escolhe o melhor hit entre os 3 primeiros do BLAST, pulando headers
#' "nao-informativos" (ex. "Uncultured bacterium"). Se os 3 forem ruins (ou
#' ausentes), marca como "Match_not_reliable" em vez de forcar uma
#' identificacao a partir de um hit inutil.
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

#' Reduz o `BLAST ID` bruto a nomenclatura binomial (Genero especie),
#' removendo ruido textual e tags de incerteza ("cf.", "nr.", "MAG:" etc.), e
#' forca casos especiais de contaminacao humana para "Homo sapiens".
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
  df %>%
    select_best_blast_hit() %>%
    clean_species_names()
}

# ---- Bloco 3: taxonomia NCBI + contaminacao ---------------------------------
# Taxonomia: porta "Retrieve Complete Lineages" / "Fill Missing Taxonomic
# Ranks" do roots_metabar (secao 7), reescrito a partir de
# scripts/functions/retrieve_taxonomy_v2.R do Gabriel (sem o source() de
# caminho absoluto de outra maquina, e usando o taxid da BLAST hit ja
# selecionada em vez de resolver genero -> taxid via retrieve_taxid.R, que
# se torna desnecessario porque 1/2/3_staxid ja vem no CSV bruto).
# Contaminacao: porta a secao 10 (Fold Change vs. controles).

TAXONOMY_RANKS <- c(
  "Superkingdom (NCBI)", "Kingdom (NCBI)", "Phylum (NCBI)",
  "Subphylum (NCBI)", "Class (NCBI)", "Subclass (NCBI)",
  "Order (NCBI)", "Suborder (NCBI)", "Family (NCBI)",
  "Subfamily (NCBI)", "Genus (NCBI)"
)

#' Consulta o NCBI (taxize::classification) para os taxids unicos de
#' query_taxID e devolve uma linha por taxid, uma coluna por nivel
#' taxonomico. Correcao deliberada em relacao ao roots_metabar original: o
#' rank do taxize para Bacteria/Archaea/Eukaryota/Viruses se chama
#' "superkingdom", nao "subkingdom" (o script original renomeava a partir de
#' "subkingdom", que o taxize nunca retorna nesse nivel -- parecia um typo
#' mascarado pelo fallback manual em fill_missing_ranks). Decisao do Gabriel:
#' usar o nome certo.
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

  # Ranks "ruido" que o taxize as vezes devolve e que o roots_metabar
  # descarta explicitamente por nao serem informativos (lista copiada da
  # fonte).
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

#' Propaga taxonomia faltante "de cima pra baixo" (mesma logica do
#' roots_metabar): Superkingdom preenchido por regras manuais a partir de
#' Kingdom/Phylum quando o NCBI nao devolveu esse nivel; ranks intermediarios
#' viram "{rank} of {rank superior}" quando ausentes, pra nao deixar buraco
#' na hierarquia (em vez de NA, que quebraria agrupamentos downstream).
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

#' Junta a taxonomia completa de volta pela query_taxID e corrige `BLAST ID`
#' quando o hit BLAST veio abreviado (genero em uma letra + ponto, ex.
#' "P.rivularis algo"), substituindo pelo genero completo resolvido no NCBI.
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

# --- Contaminacao (fold change vs. controles) ---

# Valores de `Type` que marcam uma linha como sendo de um controle (nao uma
# amostra ambiental real) -- lista copiada do roots_metabar, com as duas
# variantes que de fato aparecem no nosso contrato de schema
# (`Ext. Control` / `Filt. Control` / `PCR Control`) garantidas.
CONTROL_TYPE_VALUES <- c(
  "Ext. Control", "Ext. control", "Extraction Control", "Extraction control",
  "Filt. Control", "Filt. control", "Filtration Control", "Filtration control",
  "PCR Control", "PCR control",
  "Negative Control", "Negative control", "Positive Control", "Positive control",
  "Control"
)

# Coluna de referencia (qual controle esta amostra usa) -> coluna de Fold
# Change gerada para aquele tipo de controle.
CONTROL_REFERENCE_COLUMNS <- c(
  "Ext. Control"  = "FC to Ext control",
  "PCR Control"   = "FC to PCR control",
  "Filt. Control" = "FC to Filt control"
)

#' Equivalente a gtools::foldchange(num, denom) (FC = num/denom se
#' num >= denom, senao -denom/num) -- reimplementado aqui para nao adicionar
#' uma dependencia so por essa formula de uma linha.
foldchange <- function(num, denom) {
  ifelse(num >= denom, num / denom, -denom / num)
}

#' Para cada linha (ASV numa amostra real), compara com o(s) controle(s) que
#' ela referencia via Fold Change (abundancia relativa da amostra dividida
#' pela abundancia relativa maxima da MESMA ASV no controle referenciado).
#' FC abaixo do limiar em QUALQUER controle referenciado -> "Possible
#' contamination". Ausencia de match com nenhum controle (`Control
#' presence` = FALSE) nao e penalizada -- so vira "True detection" por
#' omissao, nao por evidencia.
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
    dplyr::summarise(Max_ctrl_abd = max(`Relative Abundance Sample`, na.rm = TRUE), .groups = "drop")

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
      dplyr::summarise(Max_ctrl_abd = max(Max_ctrl_abd, na.rm = TRUE), .groups = "drop")

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
  df <- run_taxonomy_lookup(df, entrez_key = entrez_key)
  flag_contamination(df, fold_change_threshold = config$contaminacao$fold_change_threshold)
}

# ---- Bloco 4: curadoria final (faixa de amplicon, pseudo-score, Metazoa) ----
# Porta a secao 11 do roots_metabar ("Final Results Curation"), com uma
# correcao ja fechada nas notas de arquitetura: o pseudo-score usa a
# identidade/cobertura do hit EFETIVAMENTE SELECIONADO (Selected_Hit_Origin,
# apos descartar bad_res_ids), nunca sempre do hit 1 como no roots_metabar
# original -- decisao do Gabriel
# (config$identificacao$pseudoscore_hit_base == "selecionado" e a unica
# opcao aceita, nao um toggle).

#' Compara `ASV Size (pb)` com a faixa esperada de amplicon do primer
#' (config `amplicon_por_primer`). Primer sem faixa configurada -> NA (nao
#' assumimos "fora da faixa" para um primer desconhecido).
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

#' Pseudo-score = (10*identidade + cobertura) / 11, calculado a partir do hit
#' EFETIVAMENTE selecionado (nao sempre do hit 1 -- ver nota acima). Sem hit
#' confiavel (Selected_Hit_Origin == "None") -> pseudo-score NA ->
#' "Unidentified".
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
        `BLASTn pseudo-score` >= thresholds$genero ~ `Genus (NCBI)`,
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

# Listas de filo/classe-alvo do roots_metabar (bentos, zooplancton,
# fitoplancton, perifiton) -- portadas por completo por decisao do Gabriel,
# mesmo sendo irrelevantes pro MiFish2 hoje (so peixes), para manter o
# sistema reutilizavel num projeto multi-primer futuro.
METAZOA_TARGET_PHYLA <- unique(c(
  # Bentos
  "Annelida", "Arthropoda", "Mollusca",
  # Zooplancton
  "Amoebozoa", "Cercozoa", "Ciliophora", "Rotifera",
  # Perifiton
  "Bacillariophyta", "Charophyta", "Chlorophyta", "Cryptophyta",
  "Cyanobacteria", "Euglenophyta", "Rhodophyta",
  # Fitoplancton
  "Dinophyta", "Ochrophyta", "Apicomplexa", "Discosea", "Endomyxa",
  "Euglenozoa", "Evosea", "Foraminifera", "Fornicata", "Haptophyta",
  "Hemimastigophora", "Heterolobosea", "Nebulidia", "Nibbleridia",
  "Oomycota", "Parabasalia", "Perkinsozoa", "Preaxostyla", "Tubulinea"
))

METAZOA_TARGET_CLASSES <- unique(c(
  # Bentos
  "Bivalvia", "Clitellata", "Gastropoda", "Insecta",
  # Zooplancton
  "Branchiopoda", "Eurotatoria", "Filosia", "Hexanauplia", "Lobosa",
  "Oligohymenophorea",
  # Perifiton
  "Bacillariophyceae", "Chlorophyceae", "Chrysophyceae",
  "Coscinodiscophyceae", "Cryptophyceae", "Cyanophyceae",
  "Euglenophyceae", "Florideophyceae", "Mediophyceae", "Ulvophyceae",
  "Zygnematophyceae",
  # Fitoplancton
  "Dinophyceae", "Trebouxiophyceae", "Actinophryidae", "Aphelidea",
  "Bigyra", "Bolidophyceae", "Breviatea", "Centroplasthelida",
  "Choanoflagellata", "Developea", "Dictyochophyceae",
  "Eustigmatophyceae", "Filasterea", "Glaucocystophyceae",
  "Hyphochytriomycetes", "Ichthyosporea", "Olisthodiscophyceae",
  "Pelagophyceae", "Phaeophyceae", "Phaeothamniophyceae",
  "Raphidophyceae", "Synurophyceae", "Xanthophyceae"
))

#' Flag `Possible Metazoa`: exclui matches nao confiaveis/ambientais e
#' Bacteria/Archaea/Viruses; inclui Metazoa (catch-all) e os grupos-alvo
#' (bentos/zooplancton/fitoplancton/perifiton) mesmo quando fora de Metazoa
#' (ex. protistas, algas). Usa `BLAST ID` no lugar do `Final ID (NCBI)` do
#' roots_metabar -- mesmo papel, nome alinhado ao nosso contrato de schema.
flag_possible_metazoa <- function(df) {
  df %>%
    dplyr::mutate(`Possible Metazoa` = dplyr::case_when(
      stringr::str_detect(`BLAST ID`, "Chordate environmental") ~ FALSE,
      stringr::str_detect(`BLAST ID`, "Match_not_reliable") ~ FALSE,
      stringr::str_detect(`Superkingdom (NCBI)`, "Bacteria|Archaea|Viruses") ~ FALSE,
      is.na(`Kingdom (NCBI)`) ~ FALSE,
      stringr::str_detect(`Superkingdom (NCBI)`, "Eukaryota") &
        (`Phylum (NCBI)` %in% METAZOA_TARGET_PHYLA) ~ TRUE,
      stringr::str_detect(`Superkingdom (NCBI)`, "Eukaryota") &
        (`Class (NCBI)` %in% METAZOA_TARGET_CLASSES) ~ TRUE,
      stringr::str_detect(`Kingdom (NCBI)`, "Metazoa") ~ TRUE,
      TRUE ~ FALSE
    ))
}

run_final_curation <- function(df, config = DEFAULT_CONFIG) {
  df %>%
    flag_amplicon_length(config$amplicon_por_primer) %>%
    compute_pseudoscore_and_identification(config$identificacao$pseudoscore_thresholds) %>%
    flag_possible_metazoa()
}

# ---- Entrada de linha de comando --------------------------------------------

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
      "Uso: Rscript curadoria_deterministica.R <entrada.csv> [--config=config.yaml] [--output=saida.csv]",
      call. = FALSE
    )
  }

  list(input = positional[1], config = config_path, output = output_path)
}

# ---- Bloco 1: setup + parsing ------------------------------------------------

#' Executa so o bloco 1 (setup/parsing): le o CSV bruto, confere colunas
#' obrigatorias, corrige metadados geograficos e calcula as 3 colunas
#' derivadas localmente. Blocos seguintes (BLAST, taxonomia, contaminacao,
#' arvore, GBIF) ainda vao ser encadeados aqui.
run_setup_parsing <- function(input_path, config = DEFAULT_CONFIG, schema = read_schema()) {
  df <- read_asv_table(input_path)
  check_required_columns(df, schema)

  df <- df %>%
    rename_metadata_columns() %>%
    compute_derived_local_columns()

  df
}

main <- function(args) {
  parsed <- parse_cli_args(args)
  config <- load_config(parsed$config)

  df <- run_setup_parsing(parsed$input, config = config)
  df <- run_blast_refinement(df)
  df <- run_taxonomy_and_contamination(df, config = config)
  df <- run_final_curation(df, config = config)

  cat(sprintf("Tabela parseada: %d linhas, %d colunas.\n", nrow(df), ncol(df)))
  cat("Colunas calculadas localmente: ASV Size (pb), Sample total abundance, ASV header.\n")
  cat("Origem do hit selecionado (Selected_Hit_Origin):\n")
  print(table(df$Selected_Hit_Origin, useNA = "always"))
  cat(sprintf(
    "BLAST ID = 'Match_not_reliable' em %d de %d linhas.\n",
    sum(df$`BLAST ID` == "Match_not_reliable"), nrow(df)
  ))
  cat("Status de contaminacao:\n")
  print(table(df$`Contamination status`, useNA = "always"))
  cat("Faixa de amplicon (Primer expected length):\n")
  print(table(df$`Primer expected length`, useNA = "always"))
  cat("Identificacao maxima (Identification Max. taxonomy):\n")
  print(table(df$`Identification Max. taxonomy`, useNA = "always"))
  cat("Possible Metazoa:\n")
  print(table(df$`Possible Metazoa`, useNA = "always"))
  print(dplyr::glimpse(df))

  if (!is.null(parsed$output)) {
    readr::write_csv2(df, parsed$output)
    cat(sprintf("Tabela parseada salva em: %s\n", parsed$output))
  }

  invisible(df)
}

if (!interactive()) {
  main(commandArgs(trailingOnly = TRUE))
}
