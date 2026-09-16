# Testa o retry com backoff exponencial de gbif_regional_count() em erro de
# limite de taxa do GBIF, e o cache por nome unico de run_regional_check() --
# ver r/curadoria_deterministica.R / .qmd (secao "Checagem regional GBIF").
# rgbif::occ_count() e mockado em todos os casos: nenhum destes testes bate
# na API real do GBIF.

test_that("limite de taxa ('Too many requests') tenta de novo e usa o resultado da 2a tentativa", {
  calls <- 0
  local_mocked_bindings(
    occ_count = function(...) {
      calls <<- calls + 1
      if (calls == 1) {
        stop("Too many requests! To download GBIF occurrence data in bulk, please use occ_download().")
      }
      42
    },
    .package = "rgbif"
  )

  result <- gbif_regional_count(
    "Astyanax fasciatus",
    wkt = "POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))",
    verbose = FALSE
  )

  expect_equal(result, 42L)
  expect_equal(calls, 2)
})

test_that("erro que nao e de limite de taxa devolve NA sem tentar de novo", {
  calls <- 0
  local_mocked_bindings(
    occ_count = function(...) {
      calls <<- calls + 1
      stop("WKT must be one of the types: POINT, POLYGON, MULTIPOLYGON, LINESTRING, LINEARRING")
    },
    .package = "rgbif"
  )

  result <- suppressWarnings(
    gbif_regional_count("Astyanax fasciatus", wkt = "NOTAWKT", verbose = FALSE)
  )

  expect_true(is.na(result))
  expect_equal(calls, 1)
})

test_that("limite de taxa persistente alem de max_retries devolve NA", {
  calls <- 0
  local_mocked_bindings(
    occ_count = function(...) {
      calls <<- calls + 1
      stop("Too many requests! To download GBIF occurrence data in bulk, please use occ_download().")
    },
    .package = "rgbif"
  )

  result <- suppressWarnings(
    gbif_regional_count(
      "Astyanax fasciatus",
      wkt = "POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))",
      verbose = FALSE,
      max_retries = 2
    )
  )

  expect_true(is.na(result))
  expect_equal(calls, 2)
})

test_that("run_regional_check consulta o GBIF uma vez por nome cientifico unico, nao por linha", {
  df <- tibble::tibble(
    Identification = c(
      "Astyanax fasciatus", "Astyanax fasciatus",
      "Salminus brasiliensis", "Genus indet."
    ),
    `Identification Max. taxonomy` = c("Species", "Species", "Species", "Genus")
  )
  config <- list(
    checagem_regional = list(
      fonte = "gbif",
      area_bbox = list(lat_min = -20, lat_max = -19, long_min = -44, long_max = -43)
    )
  )

  queried_names <- character(0)
  local_mocked_bindings(
    occ_count = function(scientificName, ...) {
      queried_names <<- c(queried_names, scientificName)
      5
    },
    .package = "rgbif"
  )

  result <- run_regional_check(df, config = config)

  # 2 nomes unicos em nivel de especie -- "Astyanax fasciatus" nao e
  # consultado duas vezes so porque aparece em duas linhas, e "Genus indet."
  # (nivel de genero) nem entra na consulta.
  expect_equal(sort(queried_names), c("Astyanax fasciatus", "Salminus brasiliensis"))
  expect_equal(
    result$`GBIF regional occurrence count`[result$Identification == "Astyanax fasciatus"],
    c(5L, 5L)
  )
})
