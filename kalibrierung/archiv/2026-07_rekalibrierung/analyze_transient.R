# =============================================================================
# analyze_transient.R  —  validate token-accurate NTC timing on a bath up/down sweep
# -----------------------------------------------------------------------------
# The temperature head measures its sensors SEQUENTIALLY, so one line spans ~30 s.
# On a ramp (dT/dt != 0) a single row timestamp mis-dates the late values in a line
# by up to  max(TOFFMS) * dT/dt  (at 1 C/min that is ~0.5 C across a 30 s line!).
# The token-accurate logger records each value's own arrival time (TOFFMS, ms from
# the first value). This script:
#   1. reads the NTC file WITH TOFFMS  -> every count gets its own absolute time,
#   2. interpolates the dense SPRT to (a) each value's TOFFMS time and (b) the row
#      time, and compares,
#   3. shows the row-time aliasing (residual slope vs TOFFMS offset ~ dT/dt) vanish
#      under per-value matching, and
#   4. extracts the NTC<->SPRT thermal lag  dTau  from  resid ~ dT/dt  (uses both
#      ramp rates automatically; hysteresis between up/down legs = 2*dTau*rate).
# Reference = universal mean-S4 curve; each sensor's residual is demeaned, so only
# the timing/lag effects remain (the per-sensor curve offset drops out).
#
# Run:  Rscript analyze_transient.R      (edit DATADIR / STEM below if needed)
# =============================================================================

REPO <- "/Users/tlaepple/sharedAI/CalibrationChains"
TZ   <- "UTC"
source(file.path(REPO, "lib", "SPRTRtoT_NTCtoR.R"))            # NTCcounts2R, S4_predict_T_C, SPRTglas_R2T
source(file.path(REPO, "lib", "ReadMicroKandGetPlateaus.R"))  # read_microk_file (adds $TSPRT, glass)
# Per-sensor calibration from the central registry (set2 covers these nodes). Using
# the real per-sensor curve means the STATIC residual is the true spatial gradient,
# not a curve mismatch. Falls back to the universal mean-S4 if a sensor is missing.
REG <- "/Users/tlaepple/sharedAI/CalibrationRegistry/R/calib_registry.R"
if (file.exists(REG)) source(REG) else message("registry not found -> mean-S4 fallback")
NTC_MEAN_COEF <- c(8.4229499262e-04, 2.7615486685e-04, -3.1654916185e-06, 3.0727486494e-07)
cal_T <- function(counts, sensor) {
  if (exists("calib_T")) { t <- tryCatch(suppressWarnings(calib_T(counts, sensor)), error = function(e) NULL)
    if (!is.null(t)) return(t) }
  S4_predict_T_C(NTCcounts2R(counts), NTC_MEAN_COEF)          # fallback
}

## ---- locate the sweep run (edit if your files live elsewhere) ---------------
CAND <- c(file.path(REPO, "recalib2026_07_03", "Output"),
          "/Users/tlaepple/sharedAI/labcode/Output",
          file.path(REPO, "recalib2026_07_03"))
ntc_files <- unlist(lapply(CAND, function(d) Sys.glob(file.path(d, "*Transient*_ntc.txt"))))
stopifnot("no *Transient*_ntc.txt found — set DATADIR/STEM" = length(ntc_files) > 0)
NTC    <- ntc_files[which.max(file.info(ntc_files)$mtime)]      # newest
MICROK <- sub("_ntc\\.txt$", "_microk.txt", NTC)
OUT    <- sub("_ntc\\.txt$", "_transient_report.pdf", NTC)
cat("NTC   :", NTC, "\nMicroK:", MICROK, "\n")

## ---- token-aware NTC reader: one row PER VALUE, with its own time -----------
read_ntc_toffms <- function(file, tz = "UTC") {
  lines <- readLines(file); header <- list(); out <- list(); k <- 0
  cells <- function(b) { v <- trimws(unlist(strsplit(b, "\\|+"))); v[nzchar(v)] }
  for (ln in lines) {
    f <- strsplit(ln, ";")[[1]]; if (length(f) < 4) next
    grp <- trimws(f[1]); secs <- trimws(f[2]); block <- trimws(f[4])
    if (secs == "SecondsElapsed") { header[[grp]] <- cells(block); next }
    if (is.na(suppressWarnings(as.numeric(secs)))) next
    if (!grepl("[0-9]", block) || grepl("NTC|New Node Array", block)) next
    labs <- header[[grp]]; if (is.null(labs)) next
    vals <- suppressWarnings(as.numeric(cells(block))); if (length(vals) != length(labs)) next
    off <- rep(0, length(vals))                          # ms per value; 0 = fall back to row time
    if (length(f) >= 5) {
      tf <- paste(f[5:length(f)], collapse = ";")
      m  <- regmatches(tf, regexpr("TOFFMS=[0-9|]+", tf))
      if (length(m)) { o <- suppressWarnings(as.numeric(strsplit(sub("TOFFMS=", "", m), "\\|")[[1]]))
        if (length(o) == length(vals)) off <- o }
    }
    t0 <- as.POSIXct(trimws(f[3]), format = "%Y-%m-%d %H:%M:%OS", tz = tz)
    k <- k + 1
    out[[k]] <- data.frame(t_row = t0, t_val = t0 + off / 1000, off_s = off / 1000,
                           label = labs, counts = vals, stringsAsFactors = FALSE)
  }
  df <- do.call(rbind, out)
  df$node <- sub("_.*$", "", df$label); df$channel <- sub("^[^_]*_", "", df$label)
  df$counts[df$counts > 1e7] <- NA
  df
}

## ---- read streams ----------------------------------------------------------
sp <- read_microk_file(MICROK, tz = TZ); sp$TC <- sp$TSPRT - 273.15
sp <- sp[order(sp$timestamp), ]
sprt_at <- approxfun(as.numeric(sp$timestamp), sp$TC, rule = 2)
# local dT/dt of the SPRT (mK/s), interpolated to any time
sp$dTdt <- c(NA, diff(sp$TC) / as.numeric(diff(sp$timestamp), units = "secs"))
dtdt_at <- approxfun(as.numeric(sp$timestamp[-1]), sp$dTdt[-1] * 1000, rule = 2)

nt <- read_ntc_toffms(NTC, tz = TZ)
nt <- nt[!is.na(nt$counts), ]
has_toff <- any(nt$off_s > 0)
cat(sprintf("NTC values: %d   sensors: %d   TOFFMS present: %s   max offset: %.1f s\n",
            nrow(nt), length(unique(nt$label)), has_toff, max(nt$off_s)))

## ---- match SPRT per value (TOFFMS time) and per row (first-value time) ------
nt$T_val <- sprt_at(as.numeric(nt$t_val))
nt$T_row <- sprt_at(as.numeric(nt$t_row))
nt$rate  <- dtdt_at(as.numeric(nt$t_val))               # mK/s at the value's time
nt$leg   <- ifelse(nt$rate < 0, "down", "up")
nt$R     <- NTCcounts2R(nt$counts)
# per-sensor registry calibration (set2); falls back to mean-S4 inside cal_T
nt$T_ntc <- mapply(cal_T, nt$counts, nt$label)
nt$res_val <- (nt$T_ntc - nt$T_val) * 1000              # mK
nt$res_row <- (nt$T_ntc - nt$T_row) * 1000
# demean per sensor so all sensors overlay (removes the per-sensor curve offset)
nt$key <- paste(nt$node, nt$channel)
dm <- function(x, key) x - ave(x, key, FUN = function(z) mean(z, na.rm = TRUE))
nt$resd_val <- dm(nt$res_val, nt$key)
nt$resd_row <- dm(nt$res_row, nt$key)

## ---- (1) row-time aliasing: residual vs offset within a line ---------------
dn <- nt[nt$leg == "down" & is.finite(nt$off_s) & is.finite(nt$resd_row), ]
sl_row <- if (nrow(dn) > 10) unname(coef(lm(resd_row ~ off_s, dn))[2]) else NA  # mK per s of offset
sl_val <- if (nrow(dn) > 10) unname(coef(lm(resd_val ~ off_s, dn))[2]) else NA
rate_dn <- median(nt$rate[nt$leg == "down"], na.rm = TRUE)
cat("\n--- (1) Intra-line row-time bias (down-leg) ---\n")
cat(sprintf("  median dT/dt (down): %.2f mK/s\n", rate_dn))
cat(sprintf("  residual slope vs TOFFMS offset:  ROW-match %.2f mK/s   (~ should equal dT/dt)\n", sl_row))
cat(sprintf("                                    VAL-match %.2f mK/s   (~ should be 0)\n", sl_val))
cat(sprintf("  => max intra-line bias removed: %.0f mK across a %.0f s line\n",
            abs(sl_row) * max(nt$off_s), max(nt$off_s)))

## ---- (2) NTC<->SPRT lag: resid_val ~ dT/dt  (slope = dTau, seconds) ---------
good <- nt[is.finite(nt$rate) & is.finite(nt$resd_val) & abs(nt$rate) > 1, ]  # skip near-flat
dtau <- if (nrow(good) > 20) unname(coef(lm(resd_val ~ rate, good))[2]) else NA  # mK/(mK/s) = s
cat("\n--- (2) NTC<->SPRT thermal lag (per-value matched) ---\n")
cat(sprintf("  resd_val ~ dT/dt slope = dTau = %.2f s  (τ_SPRT - τ_NTC)\n", dtau))
cat(sprintf("  => at 1 C/min (16.7 mK/s) up/down hysteresis ~ %.1f mK; at 2 C/min ~ %.1f mK\n",
            abs(dtau) * 16.7 * 2, abs(dtau) * 33.4 * 2))
cat(sprintf("  => a plateau drift of x mK/min biases the pair by ~%.2f * x mK\n", abs(dtau) / 60))

## ---- plots -----------------------------------------------------------------
pdf(OUT, width = 10, height = 7)
op <- par(mfrow = c(2, 2), mar = c(4, 4, 3, 1))
# a) SPRT sweep overview
plot(sp$timestamp, sp$TC, type = "l", xlab = "time", ylab = "SPRT (C)",
     main = "Bath sweep (SPRT)")
# b) row-time aliasing removed
plot(dn$off_s, dn$resd_row, pch = 16, cex = .3, col = "#d0021b55",
     xlab = "value's TOFFMS offset in line (s)", ylab = "demeaned residual (mK)",
     main = "Row-time bias vs per-value match (down-leg)")
points(dn$off_s, dn$resd_val, pch = 16, cex = .3, col = "#00000055")
if (is.finite(sl_row)) abline(lm(resd_row ~ off_s, dn), col = "#d0021b", lwd = 2)
if (is.finite(sl_val)) abline(lm(resd_val ~ off_s, dn), col = "black", lwd = 2)
abline(h = 0, lty = 3)
legend("topright", c(sprintf("row-match  (slope %.1f mK/s)", sl_row),
                     sprintf("val-match  (slope %.1f mK/s)", sl_val)),
       col = c("#d0021b", "black"), pch = 16, bty = "n", cex = .8)
# c) lag: residual vs dT/dt
plot(good$rate, good$resd_val, pch = 16, cex = .3, col = "#0033aa55",
     xlab = "dT/dt (mK/s)", ylab = "demeaned residual, per-value (mK)",
     main = sprintf("NTC<->SPRT lag: dTau = %.2f s", dtau))
if (is.finite(dtau)) abline(lm(resd_val ~ rate, good), col = "#0033aa", lwd = 2)
abline(h = 0, v = 0, lty = 3)
# d) hysteresis loop for one clean sensor
ex <- names(sort(table(nt$key[nt$channel == "NTC1"]), decreasing = TRUE))[1]
d1 <- nt[nt$key == ex, ]
plot(d1$T_val, d1$resd_val, pch = 16, cex = .4, col = ifelse(d1$leg == "down", "#d0021b", "#0033aa"),
     xlab = "SPRT temperature (C)", ylab = "demeaned residual (mK)",
     main = paste("Up/down hysteresis —", ex))
legend("topright", c("down", "up"), col = c("#d0021b", "#0033aa"), pch = 16, bty = "n", cex = .8)
par(op); dev.off()
cat("\nWrote", OUT, "\n")
