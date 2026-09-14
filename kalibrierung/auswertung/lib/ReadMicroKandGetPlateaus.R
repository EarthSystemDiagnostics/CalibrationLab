#Functions to handle SPRT/MicroK data

#' Read and preprocess a MicroK calibration file
#'
#' This function reads a semicolon-separated MicroK data file created during
#' SPRT calibration, parses the timestamp column, converts key fields to
#' numeric, and computes SPRT temperature using a user-provided function
#' \code{SPRTglas_R2T()}.
#'
#' The file is expected to contain **six columns without a header line**:
#' \code{time_minutes}, \code{timestamp}, \code{resistance},
#' \code{current}, \code{channel}, \code{index}.
#'
#' @param file Character string. Path to the MicroK data file.
#' @param tz Character. Time zone for parsing the \code{timestamp} column.
#'   Default: \code{"UTC"}.
#'
#' @details
#' This function requires that a function named \code{SPRTglas_R2T} is already
#' defined in the global environment. It should accept resistance (Ohm) as input
#' and return temperature (typically Kelvin).
#'
#' The column \code{current} is **kept as a character string** exactly as in the
#' file (e.g. `"0.56mA"`). No conversion is performed.
#'
#' The function adds a new column \code{TSPRT} with temperatures computed from
#' \code{resistance}.
#'
#' @return A data frame containing:
#'   \itemize{
#'     \item \code{time_minutes} (numeric)
#'     \item \code{timestamp} (POSIXct)
#'     \item \code{resistance} (numeric)
#'     \item \code{current} (character; unchanged)
#'     \item \code{channel} (character)
#'     \item \code{index} (integer)
#'     \item \code{TSPRT} (numeric; temperature from \code{SPRTglas_R2T})
#'   }
#'
#' @examples
#' \dontrun{
#' # assuming SPRTglas_R2T() is defined
#' df_microk <- read_microk_file("20251205-201522Calib_2025MicroK.txt")
#' plot(df_microk$timestamp, df_microk$TSPRT, type = "l")
#' }
#'
#' @export
read_microk_file <- function(file, tz = "UTC") {
  
  # --- Prüfen, ob die Umrechnungsfunktion existiert ---
  if (!exists("SPRTglas_R2T")) {
    stop("Function 'SPRTglas_R2T()' not found in the environment.")
  }
  
  # --- Datei einlesen ---
  df <- read.table(file,
                   sep = ";",
                   header = FALSE,
                   stringsAsFactors = FALSE,
                   strip.white = TRUE)
  
  # Erwartete Spaltennamen
  col_names <- c("time_minutes",
                 "timestamp",
                 "resistance",
                 "current",
                 "channel",
                 "index")
  
  if (ncol(df) != length(col_names)) {
    stop("Unexpected number of columns in MicroK file: ", file,
         "\nExpected ", length(col_names), " but got ", ncol(df))
  }
  
  names(df) <- col_names
  
  # --- Zeit konvertieren ---
  df$timestamp <- as.POSIXct(df$timestamp,
                             format = "%Y-%m-%d %H:%M:%OS",
                             tz = tz)
  
  # --- Numerische Felder ---
  df$time_minutes <- as.numeric(df$time_minutes)
  df$resistance   <- as.numeric(df$resistance)
  df$index        <- as.integer(df$index)
  
  # --- current: leave as character (no unit conversion) ---
  
  # --- SPRT-Temperatur berechnen ---
  df$TSPRT <- SPRTglas_R2T(df$resistance)
  
  df
}


#' Read and preprocess a MicroK calibration file with 2 active SPRT channels
#'
#' This function reads a semicolon-separated MicroK data file created during
#' SPRT calibration, parses the timestamp column, converts key fields to
#' numeric, and computes SPRT temperature using a user-provided function
#' \code{SPRTglas_R2T()}.
#'
#' The file is expected to contain **six columns without a header line**:
#' \code{time_minutes}, \code{timestamp}, \code{resistance},
#' \code{current}, \code{channel}, \code{index}.
#'
#' @param file Character string. Path to the MicroK data file.
#' @param tz Character. Time zone for parsing the \code{timestamp} column.
#'   Default: \code{"UTC"}.
#'
#' @details
#' This function requires that functions named \code{SPRTglas_R2T} and 
#' \code{SPRT670SL_R2T} are already
#' defined in the global environment. It should accept resistance (Ohm) as input
#' and return temperature (typically Kelvin).
#'
#' The column \code{current} is **kept as a character string** exactly as in the
#' file (e.g. `"0.56mA"`). No conversion is performed.
#'
#' The function adds a new column \code{TSPRT} with temperatures computed from
#' \code{resistance}.
#'
#' @return A data frame containing:
#'   \itemize{
#'     \item \code{time_minutes} (numeric)
#'     \item \code{timestamp} (POSIXct)
#'     \item \code{resistance} (numeric)
#'     \item \code{current} (character; unchanged)
#'     \item \code{channel} (character)
#'     \item \code{index} (integer)
#'     \item \code{TSPRT} (numeric; temperature from \code{SPRTglas_R2T})
#'   }
#'
#' @examples
#' \dontrun{
#' # assuming SPRTglas_R2T() is defined
#' df_microk <- read_microk_file("20251205-201522Calib_2025MicroK.txt")
#' plot(df_microk$timestamp, df_microk$TSPRT, type = "l")
#' }
#'
#' @export
read_microk_file2SPRTs <- function(file, tz = "UTC") {
  
  # --- Prüfen, ob die Umrechnungsfunktion existiert ---
  if (!exists("SPRTglas_R2T")) {
    stop("Function 'SPRTglas_R2T()' not found in the environment.")
  }
  if (!exists("SPRT670SL_R2T")) {
    stop("Function 'SPRT670SL_R2T()' not found in the environment.")
  }
  
  # --- Datei einlesen ---
  df <- read.table(file,
                   sep = ";",
                   header = FALSE,
                   stringsAsFactors = FALSE,
                   strip.white = TRUE)
  
  # Erwartete Spaltennamen
  col_names <- c("time_minutes",
                 "timestamp",
                 "resistance",
                 "current",
                 "channel",
                 "index")
  
  if (ncol(df) != length(col_names)) {
    stop("Unexpected number of columns in MicroK file: ", file,
         "\nExpected ", length(col_names), " but got ", ncol(df))
  }
  
  names(df) <- col_names
  
  # --- Zeit konvertieren ---
  df$timestamp <- as.POSIXct(df$timestamp,
                             format = "%Y-%m-%d %H:%M:%OS",
                             tz = tz)
  
  # --- Numerische Felder ---
  df$time_minutes <- as.numeric(df$time_minutes)
  df$resistance   <- as.numeric(df$resistance)
  df$index        <- as.integer(df$index)
  
  # --- current: leave as character (no unit conversion) ---
  
  # --- SPRT-Temperatur berechnen ---
  df$channel <- as.factor(df$channel)
  df$TSPRT <- NA
  
  df$TSPRT[df$channel == "Channel2"] <- 
    SPRTglas_R2T(df$resistance[df$channel == "Channel2"])
  
  df$TSPRT[df$channel == "Channel3"] <- 
    SPRT670SL_R2T(df$resistance[df$channel == "Channel3"])
  
  df
}



# Now what if I only want to use one SPRT for the calibration?

read_microk_fileSPRTglas <- function(file, tz = "UTC") {
  df <- read_microk_file2SPRTs(file, tz = "UTC")
  
  df$TSPRT[df$channel == "Channel3"] <- NA
  
  df
}


read_microk_fileSPRT670SL <- function(file, tz = "UTC") {
  df <- read_microk_file2SPRTs(file, tz = "UTC")
  
  df$TSPRT[df$channel == "Channel2"] <- NA
  
  df
}


#' Detect temperature plateaus in SPRT (microK) time series
#'
#' Uses changepoint detection on the SPRT temperature to segment the time
#' series into plateaus of nearly constant temperature, then filters by a
#' minimum duration.
#'
#' @param df Data frame containing at least a time column and a temperature column.
#' @param value_col Character. Name of the SPRT temperature column (default `"TSPRT"` in K).
#' @param time_col Character. Name of the time column (default `"timestamp"` as POSIXct).
#' @param min_duration_min Numeric. Minimum plateau duration to be kept (in minutes).
#' @param method Character. changepoint method passed to [changepoint::cpt.mean()].
#' @param penalty Character. Penalty type passed to [changepoint::cpt.mean()].
#'
#' @return A data frame with one row per detected plateau, containing:
#' \describe{
#'   \item{start_idx}{Index of first sample in plateau.}
#'   \item{end_idx}{Index of last sample in plateau.}
#'   \item{start_time}{Timestamp at start of plateau.}
#'   \item{end_time}{Timestamp at end of plateau.}
#'   \item{mean_T}{Mean temperature in the plateau (same units as `value_col`).}
#'   \item{duration_min}{Plateau duration in minutes.}
#' }
#'
#' @details
#' Internally uses `changepoint::cpt.mean()` with the chosen method and penalty
#' to find change points in the mean SPRT temperature. Plateaus shorter than
#' `min_duration_min` are removed.
#'
#' @examples
#' \dontrun{
#' df <- read_microk_file("20251205-201522Calib_2025MicroK.txt")
#' plateaus_long <- detect_sprt_plateaus(df, value_col = "TSPRT",
#'                                       time_col = "timestamp",
#'                                       min_duration_min = 30)
#' }
#'
#' @export
detect_sprt_plateaus <- function(df,
                                 value_col       = "TSPRT",
                                 time_col        = "timestamp",
                                 min_duration_min = 30,
                                 method          = "PELT",
                                 penalty         = "MBIC") {
  # basic checks
  if (!all(c(value_col, time_col) %in% names(df))) {
    stop("Columns ", value_col, " and/or ", time_col, " not found in df.")
  }
  
  y   <- df[[value_col]]
  t   <- df[[time_col]]
  
  if (!inherits(t, "POSIXct")) {
    stop("Time column '", time_col, "' must be POSIXct.")
  }
  
  # changepoint detection
  cp_obj        <- changepoint::cpt.mean(y, method = method, penalty = penalty)
  change_points <- changepoint::cpts(cp_obj)
  
  starts <- c(1, change_points + 1)
  ends   <- c(change_points, length(y))
  
  plateaus <- data.frame(
    start_idx  = starts,
    end_idx    = ends,
    start_time = t[starts],
    end_time   = t[ends],
    mean_T     = vapply(seq_along(starts), function(i) {
      mean(y[starts[i]:ends[i]], na.rm = TRUE)
    }, numeric(1))
  )
  
  plateaus$duration_min <- as.numeric(plateaus$end_time - plateaus$start_time,
                                      units = "mins")
  
  # filter by duration
  plateaus_long <- subset(plateaus, duration_min >= min_duration_min)
  
  plateaus_long
}