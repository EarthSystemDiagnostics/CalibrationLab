#Plot Plateaus for diagnosis

plotplateau1 <- function(i,
                         df_ntc    = df_head,
                         df_tsprt  = df,
                         plateaus  = plateaus_long,
                         time_ntc_col = "DateTimePC",
                         time_T_col   = "timestamp",
                         T_col        = "TSPRT")
{
  ## Trim settings
  REMOVE_START <- 30 * 60   # sec
  REMOVE_END   <- 10 * 60
  
  start_t <- plateaus$start_time[i] + REMOVE_START
  end_t   <- plateaus$end_time[i]   - REMOVE_END
  
  if (start_t >= end_t) {
    plot.new()
    title(main = paste("Plateau", i, "too short"))
    return(invisible(NULL))
  }
  
  ## Select window
  ntc_mask <- df_ntc[[time_ntc_col]] >= start_t & df_ntc[[time_ntc_col]] <= end_t
  ts_mask  <- df_tsprt[[time_T_col]] >= start_t & df_tsprt[[time_T_col]] <= end_t
  
  df_ntc_p <- df_ntc[ntc_mask, ]
  df_T_p   <- df_tsprt[ts_mask, ]
  
  if (nrow(df_ntc_p)==0 || nrow(df_T_p)==0) {
    plot.new()
    title(main = paste("Plateau", i, "— no data"))
    return(invisible(NULL))
  }
  
  ## NTC columns
  ntc_cols <- grep("NTC", names(df_ntc_p), value=TRUE)
  n_ntc <- length(ntc_cols)
  
  ## ==== COLOR PAIRS ====
  # 8-color palette, grouped in pairs
  color_pairs <- c(
    "#1b9e77", "#66c2a5",   # green pair
    "#d95f02", "#fc8d62",   # orange pair
    "#7570b3", "#8da0cb",   # purple pair
    "#e7298a", "#e78ac3"    # pink pair
  )
  cols <- rep(color_pairs, length.out = n_ntc)
  
  ## Convert NTC counts → temperature
  ntc_temp_mat <- sapply(ntc_cols, function(cc) NTCcounts2temp(df_ntc_p[[cc]]))
  
  ## SPRT
  tsprt_temp <- df_T_p[[T_col]] - 273.15
  mean_sprt <- mean(tsprt_temp, na.rm = TRUE)
  
  ## Anomalies (mK)
  ntc_anom_mat <- sweep(ntc_temp_mat, 2, colMeans(ntc_temp_mat, na.rm=TRUE)) * 1000
  tsprt_anom   <- (tsprt_temp - mean_sprt) * 1000
  
  ylim_anom <- range(c(ntc_anom_mat, tsprt_anom), na.rm = TRUE)
  
  ## ==== PLOT ====
  plot(df_ntc_p[[time_ntc_col]], ntc_anom_mat[,1],
       pch=16, cex=0.4, col=cols[1],
       xlab="Time", ylab="Anomaly (mK)",
       main="",
       ylim=ylim_anom, xlim=c(start_t, end_t))
  
  ## SPRT anomaly (black line)
  lines(df_T_p[[time_T_col]], tsprt_anom, lwd=1, col="black")
  
  ## Other NTC lines
  if (n_ntc > 1) {
    for (k in 2:n_ntc) {
      points(df_ntc_p[[time_ntc_col]], ntc_anom_mat[,k],
             pch=16, cex=0.4, col=cols[k])
    }
  }
  
  
  ## ==== Extra title: mean SPRT temperature ====
  mtext(sprintf("Mean SPRT temperature: %.3f °C", mean_sprt),
        side = 3, line = 0.3, cex = 0.8)
  
  ## Legend
  legend("topleft",
         legend=c(ntc_cols, "TSPRT"),
         col   = c(cols, "black"),
         pch   = c(rep(16, n_ntc), NA),
         lty   = c(rep(NA, n_ntc), 1),
         bty="n", cex=0.6)
}


plot_all_plateaus <- function(plateaus = plateaus_long,
                              df_ntc   = df_head,
                              df_tsprt = df,
                              out_prefix = "plateaus_",
                              per_page = 8) {
  
  n_plateaus <- nrow(plateaus)
  
  starts <- seq(1, n_plateaus, by = per_page)
  
  for (s in starts) {
    e <- min(s + per_page - 1, n_plateaus)
    
    pdf(file = sprintf("%s%02d-%02d.pdf", out_prefix, s, e),
        width = 8.27, height = 11.69)  # A4 Hochformat
    
    par(mfrow = c(4, 2),
        mar = c(3, 3, 2, 1),
        oma = c(2, 2, 2, 2))
    
    for (i in s:e) {
      # jeder Plot ein Plateau
      plotplateau1(i,
                   df_ntc   = df_ntc,
                   df_tsprt = df_tsprt,
                   plateaus = plateaus)
      mtext(paste("Plateau", i), side = 3, line = 0.2, adj = 0, cex = 0.7)
    }
    
    dev.off()
  }
}

