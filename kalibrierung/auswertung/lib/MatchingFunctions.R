##' Match NTC temperature measurements to the nearest SPRT measurement in time.
##'
##' This function takes the time series from one NTC channel and finds, for every
##' NTC timestamp, the *temporally closest* SPRT temperature measurement.
##'
##' Matching is performed by:
##' 1. Converting timestamps to numerical seconds.
##' 2. Using \code{findInterval()} to find the position in the SPRT time vector.
##' 3. Checking both candidate neighbors (\eqn{j} and \eqn{j+1}) and selecting the one
##'    with minimal absolute time difference.
##'
##' The output is a data frame containing the NTC time, NTC raw counts,
##' NTC temperature (converted using \code{NTCcounts2temp()}),
##' matched SPRT time, matched SPRT temperature, and the time difference (in seconds).
##'
##' @param iNTC Integer index selecting which NTC channel to use.  
##'   The function automatically detects available NTC columns using a pattern match.
##'
##' @param df_ntc Data frame containing NTC data, including timestamps and NTC-count columns.
##' @param df_tsprt Data frame containing SPRT timestamps and temperatures.
##'
##' @param time_ntc_col Character name of the column in \code{df_ntc} containing timestamps.
##' @param time_T_col Character name of the column in \code{df_tsprt} containing timestamps.
##' @param T_col Character name of the SPRT temperature column in Kelvin.
##'
##' @return A data frame with one row per NTC measurement, containing:
##' \itemize{
##'   \item \code{time_ntc} — NTC timestamp  
##'   \item \code{NTC_name} — the NTC column used  
##'   \item \code{NTC_counts} — raw NTC ADC counts  
##'   \item \code{NTC_temp_C} — NTC temperature in °C  
##'   \item \code{time_sprt} — matched SPRT timestamp  
##'   \item \code{TSPRT_temp_C} — matched SPRT temperature in °C  
##'   \item \code{dt_sec} — (SPRT_time – NTC_time) in seconds  
##' }
##'
##' @examples
##' # Match NTC channel 1 to SPRT time series:
##' pairs <- match_ntc_to_sprt(iNTC = 1,
##'                             df_ntc = df_head,
##'                             df_tsprt = df)
##'
##' # Inspect time differences:
##' hist(pairs$dt_sec, breaks = 100)
##'
##' @author Thomas Laepple / GPT
match_ntc_to_sprt <- function(iNTC        = 1,
                              df_ntc      = df_head,
                              df_tsprt    = df,
                              time_ntc_col = "DateTimePC",
                              time_T_col   = "timestamp",
                              T_col        = "TSPRT") {
  
  ## ----- Identify NTC column requested -----
  ntc_cols <- grep("NTC", names(df_ntc), value = TRUE)
  if (length(ntc_cols) == 0)
    stop("No NTC columns found in df_ntc.")
  if (iNTC < 1 || iNTC > length(ntc_cols))
    stop("iNTC outside range 1..", length(ntc_cols))
  
  ntc_name <- ntc_cols[iNTC]
  
  ## ----- Extract and clean NTC data -----
  t_ntc      <- df_ntc[[time_ntc_col]]
  ntc_counts <- df_ntc[[ntc_name]]
  
  ok_ntc     <- !is.na(t_ntc) & !is.na(ntc_counts)
  t_ntc      <- t_ntc[ok_ntc]
  ntc_counts <- ntc_counts[ok_ntc]
  
  ## NTC temperature in °C
  ntc_temp_C <- NTCcounts2temp(ntc_counts)
  
  ## ----- Extract and clean SPRT data -----
  t_T    <- df_tsprt[[time_T_col]]
  T_sprt <- df_tsprt[[T_col]] - 273.15  # convert to °C
  
  ok_T   <- !is.na(t_T) & !is.na(T_sprt)
  t_T    <- t_T[ok_T]
  T_sprt <- T_sprt[ok_T]
  
  ## ----- Convert times to numeric for matching -----
  t_ntc_num <- as.numeric(t_ntc)
  t_T_num   <- as.numeric(t_T)
  
  ## ----- First-pass matching using findInterval -----
  j <- findInterval(t_ntc_num, t_T_num)
  nearest_idx <- integer(length(t_ntc_num))
  
  ## ----- Refine by checking closest neighbor -----
  for (k in seq_along(t_ntc_num)) {
    j0 <- j[k]
    cand <- c(j0, j0 + 1)
    cand <- cand[cand >= 1 & cand <= length(t_T_num)]
    
    if (length(cand) == 1) {
      nearest_idx[k] <- cand
    } else {
      dt <- abs(t_ntc_num[k] - t_T_num[cand])
      nearest_idx[k] <- cand[which.min(dt)]
    }
  }
  
  dt_sec <- t_T_num[nearest_idx] - t_ntc_num
  
  ## ----- Construct result dataframe -----
  data.frame(
    time_ntc       = t_ntc,
    NTC_name       = ntc_name,
    NTC_counts     = ntc_counts,
    NTC_temp_C     = ntc_temp_C,
    time_sprt      = t_T[nearest_idx],
    TSPRT_temp_C   = T_sprt[nearest_idx],
    dt_sec         = dt_sec
  )
}


##' Filter matched NTC–SPRT pairs to keep only measurements inside plateaus
##' and with small NTC–SPRT time differences.
##'
##' This function takes the output of \code{match_ntc_to_sprt()} and returns a
##' logical mask indicating which rows lie inside temperature plateaus *and*
##' also satisfy a maximum allowed time offset between NTC and SPRT
##' measurements.
##'
##' For each plateau:
##' \itemize{
##'   \item \code{remove_start_min} minutes are trimmed off the start,
##'   \item \code{remove_end_min} minutes are trimmed off the end,
##'   \item NTC timestamps inside the trimmed interval are selected.
##' }
##'
##' Additionally, this function enforces:
##' \deqn{|dt\_sec| \le tol\_sec}
##' so that only well-matched NTC–SPRT pairs are retained.
##'
##' @param pairs Data frame returned by \code{match_ntc_to_sprt()}, containing
##'   \code{time_ntc} and \code{dt_sec}.
##'
##' @param plateaus Data frame (e.g. \code{plateaus_long}) containing
##'   \code{start_time} and \code{end_time} as POSIXct values.
##'
##' @param remove_start_min Minutes trimmed from the start of each plateau.
##' @param remove_end_min Minutes trimmed from the end of each plateau.
##' @param tol_sec Maximum allowed |time difference| between NTC and SPRT.
##'
##' @return A logical vector of length \code{nrow(pairs)} indicating whether
##'   each measurement lies inside a trimmed plateau *and* satisfies the
##'   time-difference constraint.
##'
##' @examples
##' pairs1 <- match_ntc_to_sprt(iNTC = 1)
##'
##' mask <- filter_pairs_in_plateaus_dt(pairs1, plateaus_long,
##'                                     remove_start_min = 40,
##'                                     remove_end_min   = 40,
##'                                     tol_sec = 5)
##'
##' plot(pairs1$time_ntc[mask], pairs1$NTC_temp_C[mask], pch = 16, cex = 0.5)
##'
##' @author Thomas Laepple / GPT
filter_pairs_in_plateaus_dt <- function(pairs,
                                        plateaus = plateaus_long,
                                        remove_start_min = 30,
                                        remove_end_min   = 10,
                                        tol_sec = 5) {
  
  if (!"dt_sec" %in% names(pairs))
    stop("pairs must contain column 'dt_sec' (from match_ntc_to_sprt).")
  
  keep <- rep(FALSE, nrow(pairs))
  t_ntc <- pairs$time_ntc
  
  for (i in seq_len(nrow(plateaus))) {
    t0 <- plateaus$start_time[i] + remove_start_min * 60
    t1 <- plateaus$end_time[i]   - remove_end_min   * 60
    
    if (t1 <= t0)
      next  # plateau too short after trimming
    
    inside_time <- t_ntc >= t0 & t_ntc <= t1
    keep <- keep | inside_time
  }
  
  ## ---- integrate dt_sec constraint ----
  inside_dt <- abs(pairs$dt_sec) <= tol_sec
  
  ## final mask
  keep & inside_dt
}




