#' Read and preprocess an NTC head data file
#'
#' This function reads a semicolon-separated NTC head logfile, automatically
#' extracts valid data lines, parses time columns, and (optionally) removes
#' columns containing Test signal/board information (\code{"TestSB"}) and/or
#' \code{"GND"} channels.
#'
#' The function assumes:
#' \itemize{
#'   \item The **first line** contains the header (column names).
#'   \item Data lines have the **same number of fields** as the header.
#'   \item The **third field** in valid data rows is numeric (this filters out
#'         meta lines and repeated headers).
#' }
#'
#' @param file Character string. Path to the NTC head logfile.
#' @param tz Character. Time zone used to parse all \code{"DateTime*"} columns.
#'   Default: \code{"UTC"}.
#' @param remove_TestSB Logical. If \code{TRUE} (default), all columns whose
#'   names contain \code{"TestSB"} are removed from the returned data frame.
#' @param remove_GND Logical. If \code{TRUE}, all columns whose names end with
#'   \code{"GND"} are removed from the returned data frame.
#'
#' @details
#' All columns are read using types inferred from the header:
#' \itemize{
#'   \item Columns whose name contains \code{"DateTime"} are read as character
#'         and then converted to \code{POSIXct}.
#'   \item \code{"SecondsElapsed"} is forced to numeric (if present).
#'   \item Remaining columns are read as numeric by default.
#' }
#'
#' After reading, optional filtering of \code{"TestSB"} and \code{"GND"} columns
#' is performed depending on \code{remove_TestSB} and \code{remove_GND}.
#'
#' @return A data frame containing:
#'   \itemize{
#'     \item All parsed columns from the file,
#'     \item With all \code{"DateTime*"} columns converted to \code{POSIXct},
#'     \item With \code{"TestSB"} and/or \code{"GND"} columns optionally removed.
#'   }
#'
#' @examples
#' \dontrun{
#' df_head <- read_ntc_head_file(
#'   file = "20251201-201357CalibHead1.txt",
#'   tz = "UTC",
#'   remove_TestSB = TRUE,
#'   remove_GND = FALSE
#' )
#' str(df_head)
#' }
#'
#' @export
read_ntc_head_file <- function(file, tz = "UTC", remove_TestSB = TRUE, remove_GND = FALSE) {
  # --- Rohzeilen lesen ---
  lines <- readLines(file)
  if (length(lines) == 0) stop("File is empty: ", file)
  
  # --- Header aus erster Zeile ---
  header_split <- trimws(strsplit(lines[1], ";")[[1]])
  n_fields_header <- length(header_split)
  
  # --- Alle Zeilen splitten ---
  split    <- strsplit(lines, ";")
  n_fields <- sapply(split, length)
  
  # Kandidaten: gleiche Feldanzahl wie Header
  cand <- which(n_fields == n_fields_header)
  
  # 3. Feld muss numerisch sein (filtert Header + Metazeilen)
  is_numeric3 <- function(x) {
    v <- suppressWarnings(as.numeric(trimws(x[3])))
    !is.na(v)
  }
  
  keep_idx <- cand[sapply(split[cand], is_numeric3)]
  if (length(keep_idx) == 0)
    stop("No valid numeric data lines found in: ", file)
  
  data_lines <- lines[keep_idx]
  
  # --- colClasses vorbereiten ---
  col_names <- header_split
  classes <- rep("numeric", length(col_names))
  
  # "DateTime" Spalten als character einlesen
  dt_idx <- grep("DateTime", col_names, ignore.case = TRUE)
  classes[dt_idx] <- "character"
  
  # SecondsElapsed numeric (falls vorhanden)
  if ("SecondsElapsed" %in% col_names)
    classes[col_names == "SecondsElapsed"] <- "numeric"
  
  # --- Tabelle einlesen ---
  df_head <- read.table(text        = data_lines,
                        sep         = ";",
                        header      = FALSE,
                        strip.white = TRUE,
                        col.names   = col_names,
                        colClasses  = classes)
  
  # --- Zeitspalten in POSIXct umwandeln ---
  for (cn in col_names[dt_idx]) {
    df_head[[cn]] <- as.POSIXct(df_head[[cn]],
                                format = "%Y-%m-%d %H:%M:%OS",
                                tz = tz)
  }
  
  # ============================================================
  # TestSB-Spalten entfernen (falls gewünscht)
  # ============================================================
  if (remove_TestSB) {
    testSB_cols <- grep("TestSB", names(df_head), value = TRUE)
    df_head <- df_head[, !(names(df_head) %in% testSB_cols), drop = FALSE]
  }
  
  # Optional: auch GND-Spalten entfernen
  if (remove_GND) {
    gnd_cols <- grep("GND$", names(df_head), value = TRUE)
    df_head <- df_head[, !(names(df_head) %in% gnd_cols), drop = FALSE]
  }
  
  df_head
}