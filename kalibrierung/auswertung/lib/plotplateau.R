#' Plot NTC temperature anomalies (raw or calibrated) against SPRT for one plateau
#'
#' Visualises all NTC channels in a given plateau, either using raw
#' NTC-to-temperature conversion or Steinhart–Hart calibrated temperatures,
#' and compares them to the SPRT reference thermometer.
#'
#' @param i Integer index of the plateau to plot (row in \code{plateaus}).
#' @param df_ntc Data frame with NTC head data (from \code{read_ntc_head_file()}).
#' @param df_tsprt Data frame with SPRT data and \code{TSPRT} column
#'   (from \code{read_microk_file()}).
#' @param plateaus Data frame with at least \code{start_time} and \code{end_time}
#'   columns (POSIXct), e.g. from \code{detect_sprt_plateaus()}.
#' @param time_ntc_col Name of the time column in \code{df_ntc}.
#' @param time_T_col Name of the time column in \code{df_tsprt}.
#' @param T_col Name of the SPRT temperature column in \code{df_tsprt} (Kelvin).
#' @param cal_list Optional list of NTC calibrations (e.g. \code{cal_all$calib_list})
#'   as returned by \code{calibrate_all_ntc_S4()}. Required if
#'   \code{use_calibrated = TRUE}.
#' @param use_calibrated Logical; if TRUE, use Steinhart–Hart calibrated NTC
#'   temperatures, otherwise use raw \code{NTCcounts2temp()}.
#'
#' @return No return value; the function produces a plot.
#' @export
plotplateau <- function(i,
                        df_ntc,
                        df_tsprt,
                        plateaus,
                        time_ntc_col = "DateTimePC",
                        time_T_col   = "timestamp",
                        T_col        = "TSPRT",
                        cal_list     = NULL,
                        use_calibrated = TRUE) {
  ## ---- Trim settings (seconds) ----
  REMOVE_START <- 30 * 60
  #REMOVE_END   <- 10 * 60
  REMOVE_END   <- 5 * 60
  
  ## ---- Basic checks on plateau index ----
  if (i < 1L || i > NROW(plateaus)) {
    plot.new()
    title(main = paste("Plateau", i, "— index out of range"))
    return(invisible(NULL))
  }
  
  st_i <- plateaus$start_time[i]
  et_i <- plateaus$end_time[i]
  
  ## ---- Handle NA start/end times safely ----
  if (is.na(st_i) || is.na(et_i)) {
    plot.new()
    title(main = paste("Plateau", i, "— NA start/end time"))
    return(invisible(NULL))
  }
  
  start_t <- st_i + REMOVE_START
  end_t   <- et_i - REMOVE_END
  
  if (is.na(start_t) || is.na(end_t) || start_t >= end_t) {
    plot.new()
    title(main = paste("Plateau", i, "— too short or invalid after trimming"))
    return(invisible(NULL))
  }
  
  ## ---- Subset window ----
  ntc_mask <- df_ntc[[time_ntc_col]] >= start_t & df_ntc[[time_ntc_col]] <= end_t
  ts_mask  <- df_tsprt[[time_T_col]] >= start_t & df_tsprt[[time_T_col]] <= end_t
  
  ## ntc_mask/ts_mask können NAs enthalten -> NAs in FALSE umwandeln
  ntc_mask[is.na(ntc_mask)] <- FALSE
  ts_mask[is.na(ts_mask)]   <- FALSE
  
  df_ntc_p <- df_ntc[ntc_mask, , drop = FALSE]
  df_T_p   <- df_tsprt[ts_mask, , drop = FALSE]
  
  if (NROW(df_ntc_p) == 0L || NROW(df_T_p) == 0L) {
    plot.new()
    title(main = paste("Plateau", i, "— no data in trimmed window"))
    return(invisible(NULL))
  }
  
  ## ---- NTC columns ----
  ntc_cols <- grep("NTC", names(df_ntc_p), value = TRUE)
  n_ntc    <- length(ntc_cols)
  
  if (n_ntc == 0L) {
    plot.new()
    title(main = paste("Plateau", i, "— no NTC columns"))
    return(invisible(NULL))
  }
  
  ## ---- Colour pairs (cycled) ----
  color_pairs <- c(
    "#1b9e77", "#66c2a5",   # green pair
    "#d95f02", "#fc8d62",   # orange pair
    "#7570b3", "#8da0cb",   # purple pair
    "#e7298a", "#e78ac3"    # pink pair
  )
  cols_all <- rep(color_pairs, length.out = n_ntc)
  
  ## ---- NTC temperature: raw vs calibrated ----
  if (use_calibrated) {
    if (is.null(cal_list)) {
      stop("use_calibrated = TRUE but cal_list is NULL")
    }
    
    ntc_temp_mat <- sapply(ntc_cols, function(cc) {
      NTCcounts2temp_calibrated(
        counts   = df_ntc_p[[cc]],
        ntc_name = cc,
        cal_list = cal_list
      )
    })
  } else {
    ntc_temp_mat <- sapply(ntc_cols, function(cc) {
      NTCcounts2temp(df_ntc_p[[cc]])
    })
  }
  
  ntc_temp_mat <- as.matrix(ntc_temp_mat)
  
  ## ---- Drop sensors without usable data ----
  valid_cols <- colSums(!is.na(ntc_temp_mat)) > 0
  if (!any(valid_cols)) {
    plot.new()
    title(main = paste("Plateau", i, "— no usable NTC data"))
    return(invisible(NULL))
  }
  
  ntc_temp_mat <- ntc_temp_mat[, valid_cols, drop = FALSE]
  ntc_cols     <- ntc_cols[valid_cols]
  cols         <- cols_all[valid_cols]
  n_ntc        <- length(ntc_cols)
  
  ## ---- SPRT temperature (°C) ----
  tsprt_temp <- df_T_p[[T_col]] - 273.15
  mean_sprt  <- mean(tsprt_temp, na.rm = TRUE)
  
  ## ---- Anomalies relative to mean SPRT ----
  ntc_anom_mat <- (ntc_temp_mat - mean_sprt) * 1000
  tsprt_anom   <- (tsprt_temp  - mean_sprt) * 1000
  
  ylim_anom <- range(c(ntc_anom_mat, tsprt_anom), na.rm = TRUE)
  
  ## ---- Plot ----
  plot(df_ntc_p[[time_ntc_col]], ntc_anom_mat[, 1],
       pch = 16, cex = 0.4, col = cols[1],
       xlab = "Time", ylab = "ΔT (mK, rel. SPRT mean)",
       main = if (use_calibrated) {
         paste("Plateau", i, "— calibrated NTC vs SPRT")
       } else {
         paste("Plateau", i, "— raw NTC vs SPRT")
       },
       ylim = ylim_anom, xlim = c(start_t, end_t))
  
  ## SPRT anomaly
  lines(df_T_p[[time_T_col]], tsprt_anom, col = "black", lwd = 1.5)
  
  ## Remaining NTCs
  if (n_ntc > 1L) {
    for (k in 2:n_ntc) {
      points(df_ntc_p[[time_ntc_col]], ntc_anom_mat[, k],
             pch = 16, cex = 0.4, col = cols[k])
    }
  }
  
  ## mean SPRT temperature
  mtext(sprintf("Mean SPRT temperature: %.3f °C", mean_sprt),
        side = 3, line = 0.3, cex = 0.7)
  
  legend("topleft",
         legend = c(ntc_cols, "TSPRT"),
         col    = c(cols, "black"),
         pch    = c(rep(16, n_ntc), NA),
         lty    = c(rep(NA, n_ntc), 1),
         bty    = "n", cex = 0.7)
}

#################################################################################

# plotplateau function for using manual calibration coefficients

plotplateau_manual <- function(i,
                        df_ntc,
                        df_tsprt,
                        plateaus,
                        time_ntc_col = "DateTimePC",
                        time_T_col   = "timestamp",
                        T_col        = "TSPRT",
                        cal_list     = NULL,
                        use_calibrated = TRUE) {
  ## ---- Trim settings (seconds) ----
  REMOVE_START <- 30 * 60
  #REMOVE_END   <- 10 * 60
  REMOVE_END   <- 5 * 60
  
  ## ---- Basic checks on plateau index ----
  if (i < 1L || i > NROW(plateaus)) {
    plot.new()
    title(main = paste("Plateau", i, "— index out of range"))
    return(invisible(NULL))
  }
  
  st_i <- plateaus$start_time[i]
  et_i <- plateaus$end_time[i]
  
  ## ---- Handle NA start/end times safely ----
  if (is.na(st_i) || is.na(et_i)) {
    plot.new()
    title(main = paste("Plateau", i, "— NA start/end time"))
    return(invisible(NULL))
  }
  
  start_t <- st_i + REMOVE_START
  end_t   <- et_i - REMOVE_END
  
  if (is.na(start_t) || is.na(end_t) || start_t >= end_t) {
    plot.new()
    title(main = paste("Plateau", i, "— too short or invalid after trimming"))
    return(invisible(NULL))
  }
  
  ## ---- Subset window ----
  ntc_mask <- df_ntc[[time_ntc_col]] >= start_t & df_ntc[[time_ntc_col]] <= end_t
  ts_mask  <- df_tsprt[[time_T_col]] >= start_t & df_tsprt[[time_T_col]] <= end_t
  
  ## ntc_mask/ts_mask können NAs enthalten -> NAs in FALSE umwandeln
  ntc_mask[is.na(ntc_mask)] <- FALSE
  ts_mask[is.na(ts_mask)]   <- FALSE
  
  df_ntc_p <- df_ntc[ntc_mask, , drop = FALSE]
  df_T_p   <- df_tsprt[ts_mask, , drop = FALSE]
  
  if (NROW(df_ntc_p) == 0L || NROW(df_T_p) == 0L) {
    plot.new()
    title(main = paste("Plateau", i, "— no data in trimmed window"))
    return(invisible(NULL))
  }
  
  ## ---- NTC columns ----
  ntc_cols <- grep("NTC", names(df_ntc_p), value = TRUE)
  n_ntc    <- length(ntc_cols)
  
  if (n_ntc == 0L) {
    plot.new()
    title(main = paste("Plateau", i, "— no NTC columns"))
    return(invisible(NULL))
  }
  
  ## ---- Colour pairs (cycled) ----
  color_pairs <- c(
    "#1b9e77", "#66c2a5",   # green pair
    "#d95f02", "#fc8d62",   # orange pair
    "#7570b3", "#8da0cb",   # purple pair
    "#e7298a", "#e78ac3"    # pink pair
  )
  cols_all <- rep(color_pairs, length.out = n_ntc)
  
  ## ---- NTC temperature: raw vs calibrated ----
  if (use_calibrated) {
    if (is.null(cal_list)) {
      stop("use_calibrated = TRUE but cal_list is NULL")
    }
    
    ntc_temp_mat <- sapply(ntc_cols, function(cc) {
      NTCcounts2temp_calibrated_manual(
        counts   = df_ntc_p[[cc]],
        ntc_name = cc,
        cal_list = cal_list
      )
    })
  } else {
    ntc_temp_mat <- sapply(ntc_cols, function(cc) {
      NTCcounts2temp(df_ntc_p[[cc]])
    })
  }
  
  ntc_temp_mat <- as.matrix(ntc_temp_mat)
  
  ## ---- Drop sensors without usable data ----
  valid_cols <- colSums(!is.na(ntc_temp_mat)) > 0
  if (!any(valid_cols)) {
    plot.new()
    title(main = paste("Plateau", i, "— no usable NTC data"))
    return(invisible(NULL))
  }
  
  ntc_temp_mat <- ntc_temp_mat[, valid_cols, drop = FALSE]
  ntc_cols     <- ntc_cols[valid_cols]
  cols         <- cols_all[valid_cols]
  n_ntc        <- length(ntc_cols)
  
  ## ---- SPRT temperature (°C) ----
  tsprt_temp <- df_T_p[[T_col]] - 273.15
  mean_sprt  <- mean(tsprt_temp, na.rm = TRUE)
  
  ## ---- Anomalies relative to mean SPRT ----
  ntc_anom_mat <- (ntc_temp_mat - mean_sprt) * 1000
  tsprt_anom   <- (tsprt_temp  - mean_sprt) * 1000
  
  ylim_anom <- range(c(ntc_anom_mat, tsprt_anom), na.rm = TRUE)
  
  ## ---- Plot ----
  plot(df_ntc_p[[time_ntc_col]], ntc_anom_mat[, 1],
       pch = 16, cex = 0.4, col = cols[1],
       xlab = "Time", ylab = "ΔT (mK, rel. SPRT mean)",
       main = if (use_calibrated) {
         paste("Plateau", i, "— calibrated NTC vs SPRT")
       } else {
         paste("Plateau", i, "— raw NTC vs SPRT")
       },
       ylim = ylim_anom, xlim = c(start_t, end_t))
  
  ## SPRT anomaly
  lines(df_T_p[[time_T_col]], tsprt_anom, col = "black", lwd = 1.5)
  
  ## Remaining NTCs
  if (n_ntc > 1L) {
    for (k in 2:n_ntc) {
      points(df_ntc_p[[time_ntc_col]], ntc_anom_mat[, k],
             pch = 16, cex = 0.4, col = cols[k])
    }
  }
  
  ## mean SPRT temperature
  mtext(sprintf("Mean SPRT temperature: %.3f °C", mean_sprt),
        side = 3, line = 0.3, cex = 0.7)
  
  legend("topleft",
         legend = c(ntc_cols, "TSPRT"),
         col    = c(cols, "black"),
         pch    = c(rep(16, n_ntc), NA),
         lty    = c(rep(NA, n_ntc), 1),
         bty    = "n", cex = 0.7)
}