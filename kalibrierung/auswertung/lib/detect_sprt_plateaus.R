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