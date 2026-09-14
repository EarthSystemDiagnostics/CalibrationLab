#Calibrate


#' Calibrate a single NTC sensor using a 4-parameter Steinhart model
#'
#' This function performs a full Steinhart–Hart (4-parameter) calibration for
#' one NTC sensor. It matches each NTC measurement to the nearest SPRT
#' temperature measurement, filters the data to include only points within
#' trimmed temperature plateaus and with acceptable time-matching error,
#' fits the 4-parameter model, and returns calibration coefficients and
#' residual diagnostics.
#'
#' @param iNTC Integer index of the NTC sensor to calibrate (1-based).
#'        The function identifies the actual column via `grep("NTC", ...)`.
#' @param df_ntc Data frame containing NTC measurements, including timestamps
#'        and raw ADC counts.
#' @param df_tsprt Data frame with SPRT measurement timestamps and temperatures.
#' @param plateaus A data frame describing plateau start/end times
#'        (typically `plateaus_long`).
#' @param remove_start_min Number of minutes trimmed from the beginning
#'        of each plateau before calibration (default: 40).
#' @param remove_end_min Number of minutes trimmed from the end
#'        of each plateau before calibration (default: 10).
#' @param tol_sec Maximum allowed absolute time difference between NTC and SPRT
#'        timestamps when matching pairs (seconds; default: 5).
#'
#' @details
#' Steps performed:
#' \enumerate{
#'   \item Match the selected NTC sensor to the nearest SPRT sample via
#'         \code{match_ntc_to_sprt()}.
#'   \item Filter matches to include only trimmed plateau regions using
#'         \code{filter_pairs_in_plateaus_dt()} and enforce \code{|dt_sec| <= tol_sec}.
#'   \item Extract valid calibration pairs (non-NA, within plateaus).
#'   \item Convert SPRT temperature to Kelvin and NTC counts to resistance.
#'   \item Fit Steinhart 4-parameter model using \code{fit.S4()}.
#'   \item Compute predicted temperatures, residuals, and residual standard deviation.
#' }
#'
#' @return A list with components:
#' \describe{
#'   \item{iNTC}{The NTC index used.}
#'   \item{NTC_name}{Column name of the NTC sensor.}
#'   \item{coef_S4}{The 4 Steinhart calibration coefficients.}
#'   \item{n_points}{Number of calibration points used.}
#'   \item{sd_resid_C}{Standard deviation of calibration residuals (°C).}
#'   \item{data}{A data frame containing:
#'         \code{T_C} (SPRT °C), \code{R_ohm} (NTC resistance),
#'         \code{T_pred_C} (predicted °C), \code{resid_C} (residual °C).}
#' }
#'
#' @examples
#' \dontrun{
#' cal <- calibrate_ntc_S4(iNTC = 3,
#'                         df_ntc = df_head,
#'                         df_tsprt = df,
#'                         plateaus = plateaus_long)
#' print(cal$coef_S4)
#' }
#'
#' @export
calibrate_ntc_S4 <- function(iNTC,
                             df_ntc      = df_head,
                             df_tsprt    = df,
                             plateaus    = plateaus_long,
                             remove_start_min = 40,
                             remove_end_min   = 10,
                             tol_sec = 5) {
  ## 1) Match this NTC to SPRT
  pairs_ntc <- match_ntc_to_sprt(
    iNTC         = iNTC,
    df_ntc       = df_ntc,
    df_tsprt     = df_tsprt,
    time_ntc_col = "DateTimePC",
    time_T_col   = "timestamp",
    T_col        = "TSPRT"
  )
  
  ## 2) Keep only points inside (trimmed) plateaus AND with |dt_sec| <= tol_sec
  mask_plateau_dt <- filter_pairs_in_plateaus_dt(
    pairs            = pairs_ntc,
    plateaus         = plateaus,
    remove_start_min = remove_start_min,
    remove_end_min   = remove_end_min,
    tol_sec          = tol_sec
  )
  
  ## 3) Extra safety: require non-NA temperatures & counts
  mask <- mask_plateau_dt &
    !is.na(pairs_ntc$TSPRT_temp_C) &
    !is.na(pairs_ntc$NTC_counts)
  
  if (!any(mask)) {
    stop("No valid calibration points for iNTC = ", iNTC)
  }
  
  ## 4) Extract calibration data
  T_C   <- pairs_ntc$TSPRT_temp_C[mask]            # °C
  R_ohm <- NTCcounts2R(pairs_ntc$NTC_counts[mask]) # Ohm
  T_K   <- T_C + 273.15                            # Kelvin
  
  ## 5) Fit Steinhart 4-parameter model
  coef_S4 <- fit.S4(T_K, R_ohm)
  
  ## 6) Residual diagnostics (in °C)
  T_pred_C <- S4_predict_T_C(R_ohm, coef_S4)
  resid_C  <- T_C - T_pred_C
  sd_resid_C <- sd(resid_C, na.rm = TRUE)
  
  ## 7) Name
  NTC_name <- unique(pairs_ntc$NTC_name)
  if (length(NTC_name) != 1) NTC_name <- NTC_name[1]
  
  list(
    iNTC        = iNTC,
    NTC_name    = NTC_name,
    coef_S4     = coef_S4,
    n_points    = length(T_C),
    sd_resid_C  = sd_resid_C,
    data        = data.frame(
      T_C      = T_C,
      R_ohm    = R_ohm,
      T_pred_C = T_pred_C,
      resid_C  = resid_C
    )
  )
}
#' Calibrate all NTC sensors in a head file using Steinhart 4-parameter fits
#'
#' This function loops over all NTC sensor channels in a given NTC head
#' data frame and performs a 4-parameter Steinhart calibration for each
#' sensor via \code{\link{calibrate_ntc_S4}}. It returns both the per-sensor
#' calibration objects and a compact summary table with basic diagnostics.
#'
#' If \code{n_ntc} is \code{NULL} (recommended), the function automatically
#' infers the number of NTC sensors by counting columns whose names contain
#' the substring \code{"NTC"} in \code{df_ntc}.
#'
#' @param n_ntc Optional integer specifying how many NTC sensors to calibrate
#'        (i.e. how many indices \code{iNTC = 1..n_ntc} to pass to
#'        \code{calibrate_ntc_S4()}). If \code{NULL} (default), the number of
#'        NTC channels is inferred from \code{df_ntc} using
#'        \code{grep("NTC", names(df_ntc))}.
#' @param df_ntc Data frame containing NTC head data (timestamps, NTC counts,
#'        and possibly other columns such as GND, TestSB, etc.).
#' @param df_tsprt Data frame with SPRT timestamps and temperatures (Kelvin
#'        or convertible to °C as used in \code{calibrate_ntc_S4()}).
#' @param plateaus Data frame describing plateau start and end times, e.g.
#'        \code{plateaus_long}, used to restrict calibration to stable
#'        temperature intervals.
#' @param remove_start_min Number of minutes trimmed from the start of each
#'        plateau before using data for calibration (default: 40).
#' @param remove_end_min Number of minutes trimmed from the end of each
#'        plateau before using data for calibration (default: 10).
#' @param tol_sec Maximum allowed absolute time difference (in seconds)
#'        between matched NTC and SPRT timestamps when constructing
#'        calibration pairs (default: 5).
#'
#' @details
#' The function:
#' \enumerate{
#'   \item Determines how many NTC channels are present (if \code{n_ntc} is
#'         \code{NULL}) by counting column names containing \code{"NTC"}.
#'   \item For each \code{iNTC = 1..n_ntc}, calls \code{\link{calibrate_ntc_S4}}
#'         with the specified trimming and time-tolerance parameters.
#'   \item Collects all calibration results in a list and constructs a
#'         summary data frame with per-sensor diagnostics (number of points
#'         and residual standard deviation in °C).
#' }
#'
#' @return A list with two elements:
#' \describe{
#'   \item{calib_list}{A named list of calibration objects (one per NTC),
#'         as returned by \code{\link{calibrate_ntc_S4}}. The list names are
#'         the NTC column names.}
#'   \item{summary}{A data frame with one row per NTC sensor containing:
#'         \code{iNTC}, \code{NTC_name}, \code{n_points}, and
#'         \code{sd_resid_C} (standard deviation of residuals in °C).}
#' }
#'
#' @examples
#' \dontrun{
#' cal_all <- calibrate_all_ntc_S4(
#'   df_ntc   = df_head,
#'   df_tsprt = df,
#'   plateaus = plateaus_long
#' )
#' cal_all$summary
#' }
#'
#' @export
calibrate_all_ntc_S4 <- function(n_ntc = NULL,
                                 df_ntc      = df_head,
                                 df_tsprt    = df,
                                 plateaus    = plateaus_long,
                                 remove_start_min = 40,
                                 remove_end_min   = 10,
                                 tol_sec = 5) {
  ## --- auto-detect number of NTC channels if n_ntc is not given ---
  if (is.null(n_ntc)) {
    ntc_cols <- grep("NTC", names(df_ntc), value = TRUE)
    if (length(ntc_cols) == 0) {
      stop("No NTC columns found in df_ntc (names containing 'NTC').")
    }
    n_ntc <- length(ntc_cols)
  }
  
  ## --- run calibration for each NTC index ---
  res_list <- lapply(seq_len(n_ntc), function(i) {
    calibrate_ntc_S4(
      iNTC            = i,
      df_ntc          = df_ntc,
      df_tsprt        = df_tsprt,
      plateaus        = plateaus,
      remove_start_min = remove_start_min,
      remove_end_min   = remove_end_min,
      tol_sec         = tol_sec
    )
  })
  
  ## --- name list entries by NTC_name if available ---
  ntc_names <- sapply(res_list, function(x) x$NTC_name)
  names(res_list) <- ntc_names
  
  ## --- build summary data.frame ---
  summary_df <- do.call(rbind, lapply(res_list, function(x) {
    data.frame(
      iNTC       = x$iNTC,
      NTC_name   = x$NTC_name,
      n_points   = x$n_points,
      sd_resid_C = x$sd_resid_C,
      sd_resid_mK = x$sd_resid_C*1000
    )
  }))
  
  list(
    calib_list = res_list,
    summary    = summary_df
  )
}

#' Plot calibration residuals for one or several NTC sensors
#'
#' This function visualizes the residuals from Steinhart 4-parameter
#' calibrations produced by \code{\link{calibrate_ntc_S4}} or
#' \code{\link{calibrate_all_ntc_S4}}.  
#' It creates a scatter plot of residual temperature error versus
#' the SPRT reference temperature for all NTC sensors.
#'
#' The function accepts either:
#' \itemize{
#'   \item a list of calibration objects (each element corresponding to one NTC), or
#'   \item the full object returned by \code{calibrate_all_ntc_S4()}, which contains
#'         the calibration list inside \code{$calib_list}.
#' }
#'
#' @param cal_all Either (a) a list of calibration objects, or (b) the full list
#'        returned by \code{calibrate_all_ntc_S4()}, containing
#'        \code{$calib_list}.
#' @param unit Character string specifying the residual unit to be plotted:
#'        \code{"C"} for °C (default) or \code{"mK"} for millikelvin.
#' @param main Main title of the plot.
#'
#' @details
#' Each calibration object must contain:
#' \describe{
#'   \item{\code{x$data$T_C}}{SPRT reference temperature in °C}
#'   \item{\code{x$data$resid_C}}{Residuals (NTC - SPRT) in °C}
#' }
#'
#' Colors are assigned automatically for each NTC sensor based on its name.
#'
#' @return
#' Invisibly returns the combined long-format data frame containing:
#' \itemize{
#'   \item \code{NTC_name} — sensor ID  
#'   \item \code{T_C} — SPRT temperature  
#'   \item \code{resid_C} — residual in °C  
#'   \item \code{resid_plot} — residual in plotting units (°C or mK)
#' }
#'
#' @examples
#' \dontrun{
#' cal_all <- calibrate_all_ntc_S4(df_ntc = df_head, df_tsprt = df, plateaus = plateaus_long)
#' plot_calibration_residuals(cal_all, unit = "mK")
#' }
#'
#' @export
plot_calibration_residuals <- function(cal_all,
                                       unit = c("C", "mK"),
                                       main = "Calibration residuals") {
  
  unit <- match.arg(unit)
  
  # Detect structure: full object or direct list of calibrations
  calib_list <- if (!is.null(cal_all$calib_list)) cal_all$calib_list else cal_all
  
  # Build combined long data frame
  df_all <- do.call(rbind, lapply(calib_list, function(x) {
    data.frame(
      NTC_name = x$NTC_name,
      T_C      = x$data$T_C,
      resid_C  = x$data$resid_C
    )
  }))
  
  # Convert units if needed
  if (unit == "mK") {
    df_all$resid_plot <- df_all$resid_C * 1000
    ylab <- "Residual (mK)"
  } else {
    df_all$resid_plot <- df_all$resid_C
    ylab <- "Residual (°C)"
  }
  
  # Colors per sensor
  ntc_levels <- unique(df_all$NTC_name)
  cols       <- rainbow(length(ntc_levels))
  col_vec    <- cols[match(df_all$NTC_name, ntc_levels)]
  
  # ---- Plot ----
  plot(df_all$T_C, df_all$resid_plot,
       pch = 16, cex = 0.5, col = col_vec,
       xlab = "SPRT temperature (°C)",
       ylab = ylab,
       main = main)
  
  abline(h = 0, lty = 2)
  
  legend("topleft",
         legend = ntc_levels,
         col    = cols,
         pch    = 16,
         cex    = 0.7,
         bty    = "n")
  
  invisible(df_all)
}


#' Calibrate all NTC sensors for multiple heads
#'
#' This function loops over several NTC head files (e.g. Head1, Head2, ...)
#' reads each head's data, and runs \code{calibrate_all_ntc_S4()} for each.
#' It returns a "calibration project" containing, for each head:
#' the original NTC data (\code{df_ntc}), the calibration results
#' (\code{cal_all}), and some bookkeeping such as file names.
#'
#' @param base_path Path where the NTC head files are stored.
#' @param base_prefix Common filename prefix, e.g. \code{"20251201-201357CalibHead"}.
#'   The full filename is built as \code{file.path(base_path, paste0(base_prefix, head_id, ".txt"))}.
#' @param head_ids Integer vector of head IDs (e.g. \code{1:4}).
#' @param df_tsprt SPRT data frame (from \code{read_microk_file()}), containing
#'   \code{timestamp} and \code{TSPRT} (Kelvin).
#' @param plateaus Data frame of plateaus, with \code{start_time} and \code{end_time}
#'   (POSIXct) used for calibration.
#' @param remove_start_min Minutes to trim from the start of each plateau.
#' @param remove_end_min Minutes to trim from the end of each plateau.
#' @param tol_sec Maximum allowed time difference (in seconds) between NTC and SPRT
#'   measurement when pairing for calibration.
#'
#' @return A list with:
#'   \describe{
#'     \item{heads}{List of heads; each head has \code{head_id}, \code{file}, 
#'                  \code{df_ntc}, and \code{cal_all}.}
#'     \item{plateaus}{The plateau table that was used.}
#'     \item{df_tsprt}{The SPRT data frame passed in.}
#'   }
#' @export
calibrate_all_heads <- function(base_path,
                                base_prefix,
                                head_ids,
                                df_tsprt,
                                plateaus,
                                remove_start_min = 40,
                                remove_end_min   = 10,
                                tol_sec          = 5) {
  heads_list <- list()
  
  for (h in head_ids) {
    file_h <- file.path(base_path,
                        paste0(base_prefix, h, ".txt"))
    
    message("Reading & calibrating head ", h, ": ", file_h)
    
    # NTC-Daten dieses Heads lesen
    df_ntc_h <- read_ntc_head_file(file_h)
    df_ntc_h <- RemoveSimpleGND(df_ntc_h)
    
    # Alle NTC dieses Heads kalibrieren
    cal_all_h <- calibrate_all_ntc_S4(
      df_ntc           = df_ntc_h,
      df_tsprt         = df_tsprt,
      plateaus         = plateaus,
      remove_start_min = remove_start_min,
      remove_end_min   = remove_end_min,
      tol_sec          = tol_sec
    )
    
    # Residual-Statistik pro NTC für diesen Head
    # (kommt aus cal_all_h$summary) und Head-ID ergänzen
    stats_h <- cal_all_h$summary
    stats_h$head_id <- h
    
    # Alles in der Head-Liste speichern
    heads_list[[length(heads_list) + 1L]] <- list(
      head_id = h,
      file    = file_h,
      df_ntc  = df_ntc_h,
      cal_all = cal_all_h,
      stats   = stats_h
    )
  }
  
  # Namen der Liste nach Head-IDs setzen (optional)
  names(heads_list) <- paste0("Head", head_ids)
  
  # Kombinierte Summary über alle Heads
  summary_all <- do.call(
    rbind,
    lapply(heads_list, function(h) h$stats)
  )
  rownames(summary_all) <- NULL
  
  list(
    heads    = heads_list,
    plateaus = plateaus,
    df_tsprt = df_tsprt,
    summary  = summary_all
  )
}

#' Plot calibration residuals for all heads into a single PDF
#'
#' Given a calibration object produced by \code{\link{calibrate_all_heads}},
#' this function creates a multi-panel PDF, where each panel shows residuals
#' (for all NTC sensors) from one head.
#'
#' @param cal_project List returned by \code{\link{calibrate_all_heads}}.
#' @param pdf_file Character. Output PDF file name.
#' @param unit Character. Residual unit: `"C"` (°C) or `"mK"` (millikelvin).
#'
#' @return Invisibly returns `NULL`.  
#'   A PDF is written to disk.
#'
#' @seealso \code{\link{calibrate_all_heads}},
#'          \code{\link{plot_calibration_residuals}}
#'
#' @export
plot_residuals_heads_pdf <- function(
    cal_project,
    pdf_file = "NTC_residuals_heads1-4.pdf",
    unit = "mK"
) {
  heads_list <- cal_project$heads
  n_heads    <- length(heads_list)
  
  # A4 size
  pdf(pdf_file, width = 8.27, height = 11.69)
  op <- par(mfrow = c(n_heads, 1), mar = c(4, 4, 2, 1))
  on.exit({
    par(op)
    dev.off()
  }, add = TRUE)
  
  for (h_name in names(heads_list)) {
    h_obj   <- heads_list[[h_name]]
    cal_all <- h_obj$cal_all
    
    plot_calibration_residuals(
      cal_all,
      unit = unit,
      main = paste("Calibration residuals (", unit, ") —", h_name)
    )
  }
  
  invisible(NULL)
}


#' Extract Steinhart–Hart S4 coefficients from a multi-head calibration project
#'
#' This function takes the output of \code{\link{calibrate_all_heads}} and
#' extracts the Steinhart–Hart 4-parameter calibration coefficients for every
#' NTC sensor in every head.  
#' It also returns the number of calibration points and the SD of residuals.
#'
#' @param cal_project List returned by \code{\link{calibrate_all_heads}}.
#'
#' @return A data frame with columns:
#'   \describe{
#'     \item{head_id}{Which head the sensor belongs to}
#'     \item{NTC_name}{Name of the NTC sensor (column name)}
#'     \item{a, b, c, d}{Steinhart–Hart coefficients}
#'     \item{n_points}{Number of calibration points used}
#'     \item{sd_resid_C}{SD of residuals in °C}
#'   }
#'
#' @seealso \code{\link{calibrate_all_heads}},
#'          \code{\link{fit.S4}},
#'          \code{\link{S4_predict_T_C}}
#'
#' @export
extract_S4_coeff_table <- function(cal_project) {
  do.call(rbind, lapply(cal_project$heads, function(h) {
    head_id <- h$head_id
    lapply(h$cal_all$calib_list, function(x) {
      cf <- x$coef_S4
      data.frame(
        head_id   = head_id,
        NTC_name  = x$NTC_name,
        a = cf[1],
        b = cf[2],
        c = cf[3],
        d = cf[4],
        n_points   = x$n_points,
        sd_resid_C = x$sd_resid_C
      )
    })
  })) -> lst
  
  do.call(rbind, unlist(lst, recursive = FALSE))
}




NTCcounts2temp_calibrated <- function(counts, ntc_name, cal_list = NULL) {
  
  # Always return numeric vector
  out <- rep(NA_real_, length(counts))
  
  # Only proceed if calibration exists
  if (!is.null(cal_list) && ntc_name %in% names(cal_list)) {
    
    coef_S4 <- cal_list[[ntc_name]]$coef_S4
    
    # Convert counts → resistance
    R_ohm <- NTCcounts2R(counts)
    
    # Predict temperature (°C)
    out <- S4_predict_T_C(R_ohm, coef_S4)
  }
  
  return(out)  # always numeric
}

NTCcounts2temp_calibrated_manual <- function(counts, ntc_name, cal_list = NULL) {
  
  # Always return numeric vector
  out <- rep(NA_real_, length(counts))
  
  # Only proceed if calibration exists
  if (!is.null(cal_list) && ntc_name %in% names(cal_list)) {
    
    coef_S4 <- cal_list[[ntc_name]]
    
    # Convert counts → resistance
    R_ohm <- NTCcounts2R(counts)
    
    # Predict temperature (°C)
    out <- S4_predict_T_C(R_ohm, coef_S4)
  }
  
  return(out)  # always numeric
}

