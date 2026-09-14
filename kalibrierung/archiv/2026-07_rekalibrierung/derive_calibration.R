# =============================================================================
# Recalibration 2026-07-03 (12 sensors) — bath runs -> per-sensor S4 calibration
# -----------------------------------------------------------------------------
# Reads the mergeable calibration runs in ./set1_12sensors/calibration/ (A,B,C --
# same 12 sensors, verified stepless; see MANIFEST.md), combines them, selects a
# clean SETTLED window per plateau (the automation's stable_ok flag is NOT trusted
# — the bath sits ~0.17 C off setpoint so it always reads False), turns each
# plateau x sensor into one (T_ref, R) point, and fits the 4-parameter Steinhart
# model per healthy sensor. Faulty sensors (e.g. N98, contact fault) are flagged
# and fall back to the stored SM2 curve. Results -> ./results/.
#
# Handles: multiplexed groups, TestSB(=NTC3), open inputs (counts>1e7),
# NTC power loss mid-plateau (-15: window is capped at the last NTC sample),
# still-settling plateaus (flagged, not fitted).
#
# Run:  Rscript derive_calibration.R
# =============================================================================

REPO <- "/Users/tlaepple/sharedAI/CalibrationChains"
BASE <- file.path(REPO, "recalib2026_07_03")
## The mergeable 12-sensor calibration runs (A,B,C) live here after the 2026-07-06
## reorg (see MANIFEST.md). This folder IS the include-list: every run in it is a
## verified-stepless part of the same-sensor calibration set. Live/other-set runs
## and offset/spot-check runs are in ../Output, ../set1_12sensors/excluded, etc.
HERE    <- file.path(BASE, "set1_12sensors", "calibration")
RESULTS <- file.path(BASE, "results"); dir.create(RESULTS, showWarnings = FALSE)
TZ   <- "UTC"

source(file.path(REPO, "lib", "SPRTRtoT_NTCtoR.R"))          # NTCcounts2R, S4_predict_T_C, fit.S4
source(file.path(REPO, "lib", "ReadMicroKandGetPlateaus.R")) # read_microk_file

## ---- knobs -----------------------------------------------------------------
NTC_MEAN_COEF <- c(8.4229499262e-04, 2.7615486685e-04, -3.1654916185e-06, 3.0727486494e-07)
QC_FAULT_C  <- 1.0     # |T - mean-S4 curve| beyond this => faulty sensor (excluded from fit)
SETTLE_MIN  <- 20      # minutes of settling trimmed from each plateau start
USE_MIN     <- 30      # length of the settled measurement window (its tail)
SD_MAX_mK   <- 15      # plateau counts as settled if SPRT sd <= this ...
DRIFT_MAX_mK<- 25      # ... and |linear drift over the window| <= this
SM_DIR <- "/Users/tlaepple/data/EncoderHeads2026/meta"

## ---- SM1/SM2 (existing head calibration) -----------------------------------
sm_tab <- lapply(c(SM1 = "SM1_CalibrationCoefficients.csv", SM2 = "SM2_CalibrationCoefficients.csv"),
                 function(f) { p <- file.path(SM_DIR, f); if (file.exists(p))
                   read.csv(p, row.names = 1, check.names = FALSE) else NULL })
sm_col <- function(node, ch) paste0(node, "_", if (ch == "TestSB") "NTC3" else ch)  # TestSB stored as NTC3
sm_coef <- function(node, ch) { col <- sm_col(node, ch)
  for (tb in sm_tab) if (!is.null(tb) && col %in% names(tb)) return(as.numeric(tb[c("a","b","c","d"), col]))
  rep(NA_real_, 4) }
sm_src <- function(node, ch) { col <- sm_col(node, ch)
  for (nm in names(sm_tab)) if (!is.null(sm_tab[[nm]]) && col %in% names(sm_tab[[nm]])) return(nm); NA }

## ---- group-aware NTC parser (efficient) ------------------------------------
read_ntc_grouped <- function(file, tz = "UTC") {
  lines <- readLines(file); header <- list()
  tv <- lv <- cv <- vector("list", length(lines)); k <- 0
  cells <- function(b) { v <- trimws(unlist(strsplit(b, "\\|+"))); v[nzchar(v)] }
  for (ln in lines) {
    f <- strsplit(ln, ";")[[1]]; if (length(f) < 4) next
    grp <- trimws(f[1]); secs <- trimws(f[2]); block <- trimws(f[4])
    if (secs == "SecondsElapsed") { header[[grp]] <- cells(block); next }
    if (is.na(suppressWarnings(as.numeric(secs)))) next
    if (!grepl("[0-9]", block) || grepl("NTC|New Node Array", block)) next
    labs <- header[[grp]]; if (is.null(labs)) next
    vals <- suppressWarnings(as.numeric(cells(block))); if (length(vals) != length(labs)) next
    k <- k + 1; tv[[k]] <- trimws(f[3]); lv[[k]] <- labs; cv[[k]] <- vals
  }
  lens <- lengths(lv[seq_len(k)])
  df <- data.frame(DateTimePC = rep(unlist(tv[seq_len(k)]), lens),
                   label = unlist(lv[seq_len(k)]), counts = unlist(cv[seq_len(k)]),
                   stringsAsFactors = FALSE)
  df$DateTimePC <- as.POSIXct(df$DateTimePC, format = "%Y-%m-%d %H:%M:%OS", tz = tz)
  df$node <- sub("_.*$", "", df$label); df$channel <- sub("^[^_]*_", "", df$label)
  df$counts[df$counts > 1e7] <- NA           # open input / disconnected
  df
}

## ---- robust plateau reader (handles both plateaus formats) ------------------
## old firmware: 8 cols (...; ds; de; stable_ok). new firmware (drift-gate): 11 cols
## (...; ds; de; gate_ok; drift_mK; sd_mK; n). In BOTH, setpoint=field2, ds=field6,
## de=field7 -> read positionally, ignore extra trailing columns.
read_plateaus <- function(file, tz = "UTC") {
  L <- readLines(file); L <- L[nzchar(trimws(L)) & !startsWith(trimws(L), "#")]
  rows <- lapply(L, function(ln) { f <- trimws(strsplit(ln, ";")[[1]])
    if (length(f) < 7) return(NULL)
    data.frame(idx = f[1], setpoint_C = as.numeric(f[2]), ds = f[6], de = f[7],
               stringsAsFactors = FALSE) })
  pl <- do.call(rbind, rows[!vapply(rows, is.null, logical(1))])
  pl$ds <- as.POSIXct(pl$ds, tz = tz); pl$de <- as.POSIXct(pl$de, tz = tz)
  pl
}

## ---- discover runs and read all streams ------------------------------------
## HERE (set1_12sensors/calibration) contains ONLY the mergeable, verified-stepless
## same-sensor runs (A,B,C). So the folder is the include-list: read every run whose
## three streams exist with real data. No experiment-name or run-stamp filtering
## needed anymore -- excluded/offset/other-set runs are physically in other folders.
stems <- sub("_plateaus\\.txt$", "", basename(Sys.glob(file.path(HERE, "*_plateaus.txt"))))
ok3 <- function(s) all(file.exists(file.path(HERE, paste0(s, c("_microk.txt","_ntc.txt"))))) &&
                   length(readLines(file.path(HERE, paste0(s, "_ntc.txt")))) > 50
stems <- stems[vapply(stems, ok3, logical(1))]
cat("Calibration runs used:", length(stems), "->", paste(sub("_.*","",stems), collapse = ", "), "\n")
runs <- lapply(stems, function(s) {
  sp <- read_microk_file(file.path(HERE, paste0(s, "_microk.txt")), tz = TZ); sp$TC <- sp$TSPRT - 273.15
  nt <- read_ntc_grouped(file.path(HERE, paste0(s, "_ntc.txt")), tz = TZ)
  pl <- read_plateaus(file.path(HERE, paste0(s, "_plateaus.txt")), tz = TZ)
  list(stem = s, sprt = sp, ntc = nt, pl = pl)
})
names(runs) <- stems

## ---- settled-window picker (per plateau) -----------------------------------
pick_settled <- function(sp, nt, ds, de) {
  ntc_t  <- nt$DateTimePC[nt$DateTimePC >= ds & nt$DateTimePC <= de & !is.na(nt$counts)]
  sprt_t <- sp$timestamp[sp$timestamp >= ds & sp$timestamp <= de]
  if (length(ntc_t) < 20 || length(sprt_t) < 20) return(NULL)
  t_end <- min(max(ntc_t), max(sprt_t))                 # cap at NTC power loss / data end
  dur   <- as.numeric(t_end - ds, units = "mins"); if (dur < 8) return(NULL)
  settle  <- min(SETTLE_MIN, 0.4 * dur)
  t_start <- max(ds + settle * 60, t_end - USE_MIN * 60)
  ws <- sp[sp$timestamp >= t_start & sp$timestamp <= t_end, ]
  if (nrow(ws) < 15) return(NULL)
  m <- as.numeric(ws$timestamp - min(ws$timestamp), units = "mins")
  drift <- unname(coef(lm(ws$TC ~ m))[2]) * (max(m) - min(m)) * 1000
  sdmK  <- sd(ws$TC) * 1000
  list(t_start = t_start, t_end = t_end, Tref_K = mean(ws$TSPRT), Tref_C = mean(ws$TC),
       sd_mK = sdmK, drift_mK = drift, win_min = as.numeric(t_end - t_start, units = "mins"),
       settled = is.finite(sdmK) && sdmK <= SD_MAX_mK && abs(drift) <= DRIFT_MAX_mK)
}

## ---- build calibration points (per-sensor TIME-MATCHED reference) -----------
## The NTCs are read in multiplexed groups (e.g. 5 nodes per group), each group at
## its own timestamps. The bath drifts/oscillates, so the window-mean SPRT differs
## from the SPRT at a group's sampling instants by up to ~2 mK (a per-group timing
## bias, constant within a group -- verified on set-2). Pair each sensor's mean R
## with the SPRT averaged over THAT sensor's sample times (linear interpolation of
## the SPRT series) instead of the window mean -> removes the timing bias.
pts <- list(); prow <- 0
for (r in runs) {
  sprt_at <- approxfun(as.numeric(r$sprt$timestamp), r$sprt$TC, rule = 2)
  for (i in seq_len(nrow(r$pl))) {
    w <- r$pl[i, ]; s <- pick_settled(r$sprt, r$ntc, w$ds, w$de); if (is.null(s)) next
    sub <- r$ntc[r$ntc$DateTimePC >= s$t_start & r$ntc$DateTimePC <= s$t_end & !is.na(r$ntc$counts), ]
    if (!nrow(sub)) next
    sub$Tmatch_C <- sprt_at(as.numeric(sub$DateTimePC))              # SPRT at each block's time
    ag  <- aggregate(cbind(counts, Tmatch_C) ~ node + channel, sub, mean)
    ag$R_mean <- NTCcounts2R(ag$counts)
    ag$Tref_C <- ag$Tmatch_C; ag$Tref_K <- ag$Tmatch_C + 273.15      # per-sensor matched reference
    ag$T_meanS4_C <- S4_predict_T_C(ag$R_mean, NTC_MEAN_COEF)
    ag$resid_mK   <- (ag$T_meanS4_C - ag$Tref_C) * 1000
    ag$T_SM_C     <- mapply(function(R,n,ch) S4_predict_T_C(R, sm_coef(n,ch)), ag$R_mean, ag$node, ag$channel)
    ag$bias_SM_mK <- round((ag$T_SM_C - ag$Tref_C) * 1000, 1)
    ag$run <- r$stem; ag$setpoint_C <- w$setpoint_C; ag$Tref_win_C <- s$Tref_C
    ag$win_min <- round(s$win_min, 0); ag$sprt_sd_mK <- round(s$sd_mK, 1)
    ag$sprt_drift_mK <- round(s$drift_mK, 1); ag$settled <- s$settled
    prow <- prow + 1; pts[[prow]] <- ag
  }
}
cal <- do.call(rbind, pts)
cal$flag <- ifelse(abs(cal$resid_mK) > QC_FAULT_C * 1000, "FAULTY", "ok")

## ---- plateau report --------------------------------------------------------
plsum <- unique(cal[, c("run","setpoint_C","Tref_win_C","win_min","sprt_sd_mK","sprt_drift_mK","settled")])
plsum <- plsum[order(plsum$Tref_win_C), ]
cat("\n--- Plateaus (settled window per plateau; Tref_win = window-mean SPRT) ---\n")
plsum$run <- sub("_.*","",plsum$run)
print(transform(plsum, Tref_win_C = round(Tref_win_C, 3)), row.names = FALSE)
nset <- sum(plsum$settled)
cat(sprintf("\n%d plateau(s) total, %d settled & usable for the fit.\n", nrow(plsum), nset))

## ---- QC: faulty sensors ----------------------------------------------------
faulty <- unique(cal[cal$flag == "FAULTY", c("node","channel")])
if (nrow(faulty)) cat(sprintf("FAULTY (excluded from fit): %s\n",
    paste(sort(unique(faulty$node)), collapse = ", ")))

write.csv(cal[order(cal$Tref_C, cal$node, cal$channel),
              c("run","setpoint_C","Tref_C","node","channel","flag","counts","R_mean",
                "resid_mK","bias_SM_mK","win_min","sprt_sd_mK","sprt_drift_mK","settled")],
          file.path(RESULTS, "calibration_points.csv"), row.names = FALSE)

## ---- per-sensor S4 fit (healthy sensors, settled plateaus, >=4 distinct T) --
## Exclude FAULTY sensors at NODE level: a node with any faulty point (e.g. N98,
## intermittent contact) is unreliable on all channels, even on plateaus where a
## channel happens to pass the 1 C gate -> force whole node to SM fallback.
faulty_nodes <- unique(cal$node[cal$flag == "FAULTY"])
fitdat <- cal[cal$flag == "ok" & cal$settled & !(cal$node %in% faulty_nodes), ]
coeffs <- do.call(rbind, lapply(split(fitdat, interaction(fitdat$node, fitdat$channel, drop = TRUE)), function(d) {
  Tk <- d$Tref_K
  node <- d$node[1]; ch <- d$channel[1]
  if (length(unique(round(Tk, 2))) < 4) return(NULL)
  co <- fit.S4(Tk, d$R_mean)
  Tp <- S4_predict_T_C(d$R_mean, co)
  data.frame(node = node, channel = ch, a = co[1], b = co[2], c = co[3], d = co[4],
             n_plateaus = nrow(d), Trange_C = sprintf("%.0f..%.0f", min(d$Tref_C), max(d$Tref_C)),
             sd_resid_mK = round(sd(d$Tref_C - Tp) * 1000, 2),
             max_resid_mK = round(max(abs(d$Tref_C - Tp)) * 1000, 2),
             source = "fit_20260703", note = "", stringsAsFactors = FALSE)
}))

## ---- faulty sensors: fall back to SM curve so the table covers all channels -
sensors_all <- unique(cal[, c("node","channel")])
fitted_key  <- if (!is.null(coeffs)) paste(coeffs$node, coeffs$channel) else character(0)
fb <- do.call(rbind, lapply(seq_len(nrow(sensors_all)), function(k) {
  nd <- sensors_all$node[k]; ch <- sensors_all$channel[k]
  if (paste(nd, ch) %in% fitted_key) return(NULL)
  co <- sm_coef(nd, ch)
  data.frame(node = nd, channel = ch, a = co[1], b = co[2], c = co[3], d = co[4],
             n_plateaus = 0L, Trange_C = NA, sd_resid_mK = NA, max_resid_mK = NA,
             source = sm_src(nd, ch),
             note = "not fitted (faulty or <4 settled plateaus) - SM curve reused",
             stringsAsFactors = FALSE)
}))
allc <- rbind(coeffs, fb)
allc <- allc[order(as.integer(sub("N","",allc$node)), allc$channel), ]
write.csv(allc, file.path(RESULTS, "calibration_coefficients.csv"), row.names = FALSE)

## ---- report ----------------------------------------------------------------
cat(sprintf("\n--- S4 fit: %d sensors freshly fitted, %d reuse SM ---\n",
            if (is.null(coeffs)) 0 else nrow(coeffs), nrow(fb)))
if (!is.null(coeffs)) {
  cat(sprintf("Fresh-fit residuals: median sd %.1f mK, worst %.1f mK\n",
              median(coeffs$sd_resid_mK), max(coeffs$max_resid_mK)))
  print(coeffs[order(-coeffs$sd_resid_mK), c("node","channel","n_plateaus","Trange_C","sd_resid_mK","max_resid_mK")][1:min(6,nrow(coeffs)),], row.names = FALSE)
}
cat(sprintf("\nWrote calibration_points.csv and calibration_coefficients.csv\n"))

## ---- diagnostic plot: fit residuals vs T -----------------------------------
if (!is.null(coeffs) && nrow(coeffs)) {
  pdf(file.path(RESULTS, "Fit_residuals.pdf"), width = 9, height = 6)
  nodes <- sort(unique(fitdat$node)); col <- setNames(rainbow(length(nodes)), nodes)
  plot(NA, xlim = range(fitdat$Tref_C), ylim = c(-1,1)*max(8, 1.1*max(coeffs$max_resid_mK)),
       xlab = "SPRT temperature (C)", ylab = "fit residual  Tref - T_fit(R)  (mK)",
       main = "Fresh S4 calibration: per-sensor fit residuals")
  abline(h = 0, lty = 2)
  for (kk in seq_len(nrow(coeffs))) {
    d <- fitdat[fitdat$node == coeffs$node[kk] & fitdat$channel == coeffs$channel[kk], ]
    co <- as.numeric(coeffs[kk, c("a","b","c","d")])
    points(d$Tref_C, (d$Tref_C - S4_predict_T_C(d$R_mean, co)) * 1000, pch = 16, cex = .6,
           col = col[d$node[1]])
  }
  legend("topright", legend = nodes, col = col, pch = 16, ncol = 2, cex = .7, bty = "n")
  dev.off()
  cat("Wrote Fit_residuals.pdf\n")
}
