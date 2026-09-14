#' Compute plateau-wise residual statistics for one NTC sensor
#'
#' Uses the same SPRT plateaus and matching logic as the calibration,
#' but aggregates per plateau:
#'  - mean SPRT temperature
#'  - mean predicted temperature
#'  - SD of predicted T and SPRT T (°C & mK)
#'  - mean residual (T_sprt - T_pred) in °C & mK
#'
#' @param cal_project Result from calibrate_all_heads()
#' @param head_id     Integer head id (e.g. 1, 2, 3, 4)
#' @param ntc_name    Name of NTC column, e.g. "N50_NTC1"
#' @param remove_start_min Minutes to trim from start of plateau (for plots)
#' @param remove_end_min   Minutes to trim from end of plateau
#' @param tol_sec          Max allowed |dt| between NTC and SPRT (as in calib)
#'
#' @return data.frame with one row per plateau and columns:
#'   plateau_id, t_start, t_end, n_points,
#'   mean_Tsprt_C, mean_R_ohm, T_pred_C,
#'   sd_T_pred_C, sd_Tsprt_C, resid_C,
#'   sd_T_pred_mK, sd_Tsprt_mK, resid_mK
compute_plateau_stats_ntc <- function(cal_project,
                                      head_id,
                                      ntc_name,
                                      remove_start_min = 40,
                                      remove_end_min   = 10,
                                      tol_sec          = 5) {
  
  plateaus <- cal_project$plateaus
  df_tsprt <- cal_project$df_tsprt
  
  # Head-Objekt holen
  head_obj <- cal_project$heads[[which(vapply(
    cal_project$heads, function(h) h$head_id, integer(1)
  ) == head_id)]]
  
  df_ntc <- head_obj$df_ntc
  
  # Index dieses NTC im Head finden (iNTC wie in calibrate_ntc_S4)
  ntc_cols <- grep("NTC", names(df_ntc), value = TRUE)
  if (!ntc_name %in% ntc_cols) {
    stop("ntc_name ", ntc_name, " not found in df_ntc columns.")
  }
  iNTC <- match(ntc_name, ntc_cols)
  
  # Kalibration für diesen NTC holen (für die Koeffizienten)
  calib_obj <- head_obj$cal_all$calib_list[[ntc_name]]
  coef_S4   <- calib_obj$coef_S4
  
  # NTC ↔ SPRT-Paare erneut matchen (wie in calibrate_ntc_S4)
  pairs_ntc <- match_ntc_to_sprt(
    iNTC         = iNTC,
    df_ntc       = df_ntc,
    df_tsprt     = df_tsprt,
    time_ntc_col = "DateTimePC",
    time_T_col   = "timestamp",
    T_col        = "TSPRT"
  )
  
  # Alle T_C & R_ohm (noch ohne Plateau-Trim)
  T_C_all   <- pairs_ntc$TSPRT_temp_C
  R_ohm_all <- NTCcounts2R(pairs_ntc$NTC_counts)
  
  # Plateau-Schleife (angepasst an deinen alten Code, aber mit mK-Spalten)
  plateau_stats <- lapply(seq_len(nrow(plateaus)), function(i) {
    t0 <- plateaus$start_time[i] + remove_start_min * 60
    t1 <- plateaus$end_time[i]   - remove_end_min   * 60
    
    if (t1 <= t0) return(NULL)  # plateau too short after trimming
    
    inside <- pairs_ntc$time_ntc >= t0 & pairs_ntc$time_ntc <= t1 &
      abs(pairs_ntc$dt_sec) <= tol_sec
    
    if (!any(inside)) return(NULL)
    
    T_C_p <- T_C_all[inside]
    R_p   <- R_ohm_all[inside]
    
    mean_Tsprt <- mean(T_C_p, na.rm = TRUE)
    mean_R     <- mean(R_p,   na.rm = TRUE)
    
    # Prediction von mittlerem R
    T_pred_C <- S4_predict_T_C(mean_R, coef_S4)
    
    # Vorhersage für alle R im Plateau
    T_pred_all_C <- S4_predict_T_C(R_p, coef_S4)
    
    sd_T_pred_C <- stats::sd(T_pred_all_C, na.rm = TRUE)
    sd_Tsprt_C  <- stats::sd(T_C_p,        na.rm = TRUE)
    
    resid_C <- mean_Tsprt - T_pred_C
    
    # in mK
    sd_T_pred_mK <- sd_T_pred_C * 1000
    sd_Tsprt_mK  <- sd_Tsprt_C  * 1000
    resid_mK     <- resid_C     * 1000
    
    data.frame(
      plateau_id     = i,
      t_start        = t0,
      t_end          = t1,
      n_points       = sum(inside),
      mean_Tsprt_C   = mean_Tsprt,
      mean_R_ohm     = mean_R,
      T_pred_C       = T_pred_C,
      sd_T_pred_C    = sd_T_pred_C,
      sd_Tsprt_C     = sd_Tsprt_C,
      resid_C        = resid_C,
      sd_T_pred_mK   = sd_T_pred_mK,
      sd_Tsprt_mK    = sd_Tsprt_mK,
      resid_mK       = resid_mK
    )
  })
  
  plateau_stats <- do.call(rbind, plateau_stats)
  rownames(plateau_stats) <- NULL
  
  plateau_stats
}


#' Compute plateau-wise residual statistics for all NTCs in one head
#'
#' @param cal_project Result from calibrate_all_heads()
#' @param head_id     Integer head id (z.B. 1, 2, 3, 4)
#' @param remove_start_min Minutes to trim from start of plateau
#' @param remove_end_min   Minutes to trim from end of plateau
#' @param tol_sec          Max allowed |dt| between NTC and SPRT
#'
#' @return data.frame mit Spalten:
#'   head_id, NTC_name, plateau_id, t_start, t_end, n_points,
#'   mean_Tsprt_C, mean_R_ohm, T_pred_C,
#'   sd_T_pred_C, sd_Tsprt_C, resid_C,
#'   sd_T_pred_mK, sd_Tsprt_mK, resid_mK
compute_plateau_stats_all_ntc <- function(cal_project,
                                          head_id,
                                          remove_start_min = 40,
                                          remove_end_min   = 10,
                                          tol_sec          = 5) {
  
  plateaus <- cal_project$plateaus
  df_tsprt <- cal_project$df_tsprt
  
  # Head-Objekt holen
  head_obj <- cal_project$heads[[which(vapply(
    cal_project$heads, function(h) h$head_id, integer(1)
  ) == head_id)]]
  
  df_ntc <- head_obj$df_ntc
  #df_ntc <- head_obj$df_head
  
  # Alle NTC-Spalten
  ntc_cols <- grep("NTC", names(df_ntc), value = TRUE)
  if (length(ntc_cols) == 0) {
    stop("No NTC columns found in df_ntc for head_id = ", head_id)
  }
  
  out_list <- lapply(ntc_cols, function(ntc_name) {
    # Index wie in calibrate_ntc_S4
    iNTC <- match(ntc_name, ntc_cols)
    
    calib_obj <- head_obj$cal_all$calib_list[[ntc_name]]
    coef_S4   <- calib_obj$coef_S4
    
    # NTC ↔ SPRT-Paare matchen
    pairs_ntc <- match_ntc_to_sprt(
      iNTC         = iNTC,
      df_ntc       = df_ntc,
      df_tsprt     = df_tsprt,
      time_ntc_col = "DateTimePC",
      time_T_col   = "timestamp",
      T_col        = "TSPRT"
    )
    
    T_C_all   <- pairs_ntc$TSPRT_temp_C
    R_ohm_all <- NTCcounts2R(pairs_ntc$NTC_counts)
    
    plateau_stats <- lapply(seq_len(nrow(plateaus)), function(i) {
      t0 <- plateaus$start_time[i] + remove_start_min * 60
      t1 <- plateaus$end_time[i]   - remove_end_min   * 60
      
      if (t1 <= t0) return(NULL)
      
      inside <- pairs_ntc$time_ntc >= t0 & pairs_ntc$time_ntc <= t1 &
        abs(pairs_ntc$dt_sec) <= tol_sec
      
      if (!any(inside)) return(NULL)
      
      T_C_p <- T_C_all[inside]
      R_p   <- R_ohm_all[inside]
      
      mean_Tsprt <- mean(T_C_p, na.rm = TRUE)
      mean_R     <- mean(R_p,   na.rm = TRUE)
      
      T_pred_C       <- S4_predict_T_C(mean_R, coef_S4)
      T_pred_all_C   <- S4_predict_T_C(R_p,    coef_S4)
      sd_T_pred_C    <- stats::sd(T_pred_all_C, na.rm = TRUE)
      sd_Tsprt_C     <- stats::sd(T_C_p,        na.rm = TRUE)
      resid_C        <- mean_Tsprt - T_pred_C
      
      sd_T_pred_mK   <- sd_T_pred_C * 1000
      sd_Tsprt_mK    <- sd_Tsprt_C  * 1000
      resid_mK       <- resid_C     * 1000
      
      data.frame(
        head_id        = head_id,
        NTC_name       = ntc_name,
        plateau_id     = i,
        t_start        = t0,
        t_end          = t1,
        n_points       = sum(inside),
        mean_Tsprt_C   = mean_Tsprt,
        mean_R_ohm     = mean_R,
        T_pred_C       = T_pred_C,
        sd_T_pred_C    = sd_T_pred_C,
        sd_Tsprt_C     = sd_Tsprt_C,
        resid_C        = resid_C,
        sd_T_pred_mK   = sd_T_pred_mK,
        sd_Tsprt_mK    = sd_Tsprt_mK,
        resid_mK       = resid_mK
      )
    })
    
    do.call(rbind, plateau_stats)
  })
  
  res <- do.call(rbind, out_list)
  rownames(res) <- NULL
  res
}


#' Plot plateau residuals vs. temperature for all NTCs in one head
#' Colored by time (t_start), different symbols per NTC
plot_plateau_residuals_all_ntc <- function(cal_project,
                                           head_id,
                                           unit = c("mK", "C"),
                                           remove_start_min = 40,
                                           remove_end_min   = 10,
                                           tol_sec          = 5) {
  unit <- match.arg(unit)
  
  ps <- compute_plateau_stats_all_ntc(
    cal_project      = cal_project,
    head_id          = head_id,
    remove_start_min = remove_start_min,
    remove_end_min   = remove_end_min,
    tol_sec          = tol_sec
  )
  
  # y-Wert je nach Einheit
  if (unit == "mK") {
    y <- ps$resid_mK
    ylab <- "Residual (mK)"
  } else {
    y <- ps$resid_C
    ylab <- "Residual (°C)"
  }
  
  # Zeit → Farbe
  t_numeric <- as.numeric(ps$t_start)
  t_norm    <- (t_numeric - min(t_numeric)) / (max(t_numeric) - min(t_numeric))
  col_fun    <- colorRampPalette(c("green", "yellow", "red"))
  cols       <- col_fun(100)
  point_cols <- cols[ pmax(1, pmin(100, floor(t_norm * 99) + 1)) ]
  
  # NTC → Symbol (falls du viele hast, recyclet R die PCHs)
  ntc_levels <- unique(ps$NTC_name)
  pchs       <- 1:length(ntc_levels)
  point_pch  <- pchs[ match(ps$NTC_name, ntc_levels) ]
  
  plot(ps$mean_Tsprt_C,
       y,
       col  = point_cols,
       pch  = point_pch,
       xlab = "Mean SPRT temperature (°C)",
       ylab = ylab,
       main = paste0("Plateau residuals, all NTCs — Head ", head_id,
                     "\n(color = time, symbol = NTC)")
  )
  abline(h = 0, lty = 2)
  
  legend("topleft",
         title = "NTC",
         legend = ntc_levels,
         pch    = pchs,
         col    = "black",
         cex    = 0.7,
         bty    = "n")
  
  invisible(ps)
}
