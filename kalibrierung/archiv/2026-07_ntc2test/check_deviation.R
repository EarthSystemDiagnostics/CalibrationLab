## Check deviation of NTC board (ntc2test) vs SPRT, using LAST calibration coeffs
## Single-temperature bath test on 2026-07-01

setwd("/Users/tlaepple/sharedAI/CalibrationChains")
source("./lib/sourceDir.R")
sourceDir("./lib", recursive = FALSE)

setwd("/Users/tlaepple/sharedAI/CalibrationChains/ntc2test")

file_microk <- "20260701-152420Test_2024MicroK.txt"
file_logger <- "20260701-152410_Test_2026Logger.txt"

## ---- 1) SPRT (reference / expectation) ----
## MicroK channel is "Channel2" -> glass SPRT
df_sprt <- read_microk_file(file_microk)     # adds TSPRT (glass SPRT, in K)
df_sprt$TSPRT_C <- df_sprt$TSPRT - 273.15

## ---- 2) NTC logger (2026Logger format: pipe-separated values in one field) ----
## Layout:  Group1 ; SecondsElapsed ; DateTimePC ; v1 | v2 | v3 || v4 | v5 | v6
read_logger_2026 <- function(file, tz = "UTC") {
  lines <- readLines(file)
  hdr   <- strsplit(lines[1], ";")[[1]]
  ## channel names are in field 4, pipe-separated (drop empty tokens from "||")
  ch    <- trimws(strsplit(hdr[4], "\\|")[[1]])
  ch    <- ch[ch != ""]
  ch    <- sub("TestSB$", "NTC3", ch)   # TestSB is the 3rd NTC on the node
  keep  <- grepl("[0-9]", lines) & grepl("\\|", lines) &
           !grepl("New Node Array", lines)
  parse_row <- function(ln) {
    f  <- strsplit(ln, ";")[[1]]
    ts <- trimws(f[3])
    vv <- suppressWarnings(as.numeric(trimws(strsplit(f[4], "\\|")[[1]])))
    vv <- vv[!is.na(vv)]
    if (length(vv) != length(ch)) return(NULL)   # skip repeated-header rows
    c(list(DateTimePC = ts), setNames(as.list(vv), ch))
  }
  rows <- lapply(lines[keep], parse_row)
  rows <- rows[!sapply(rows, is.null)]
  df <- do.call(rbind.data.frame, c(rows, stringsAsFactors = FALSE))
  df$DateTimePC <- as.POSIXct(df$DateTimePC, format = "%Y-%m-%d %H:%M:%OS", tz = tz)
  df
}

df_ntc   <- read_logger_2026(file_logger)
ntc_cols <- grep("NTC", names(df_ntc), value = TRUE)   # excludes TestSB
cat("NTC channels found:", paste(ntc_cols, collapse = ", "), "\n")

## ---- 3) Last calibration coefficients (SM2 contains N94 & N96) ----
coef_raw <- read.csv("SM2_CalibrationCoefficients.csv", row.names = 1,
                     check.names = FALSE)
## rows a,b,c,d ; columns per NTC
get_coef <- function(name) {
  as.numeric(coef_raw[c("a","b","c","d"), name])
}

## ---- 4) Define stable evaluation window ----
## Trim the first equilibration minutes; use the stable tail of the run.
t0 <- min(df_ntc$DateTimePC, na.rm = TRUE)
trim_start_min <- 25            # equilibration
win_ntc  <- df_ntc[ as.numeric(df_ntc$DateTimePC   - t0, units = "mins") >= trim_start_min, ]
win_sprt <- df_sprt[as.numeric(df_sprt$timestamp   - t0, units = "mins") >= trim_start_min, ]

T_sprt_mean <- mean(win_sprt$TSPRT_C, na.rm = TRUE)
T_sprt_sd   <- sd(  win_sprt$TSPRT_C, na.rm = TRUE)

cat(sprintf("\nEvaluation window: %.1f min .. end  (%d SPRT / %d NTC samples)\n",
            trim_start_min, nrow(win_sprt), nrow(win_ntc)))
cat(sprintf("SPRT reference T  = %.4f C   (sd = %.2f mK)\n\n",
            T_sprt_mean, T_sprt_sd*1000))

## ---- 5) For each NTC: predicted T with last calib, deviation vs SPRT ----
res <- data.frame(NTC = ntc_cols,
                  T_ntc_C = NA_real_, sd_mK = NA_real_,
                  dev_mK  = NA_real_)
for (i in seq_along(ntc_cols)) {
  nm     <- ntc_cols[i]
  counts <- win_ntc[[nm]]
  R      <- NTCcounts2R(counts)
  Tpred  <- S4_predict_T_C(R, get_coef(nm))    # last-calibration coefficients
  res$T_ntc_C[i] <- mean(Tpred, na.rm = TRUE)
  res$sd_mK[i]   <- sd(Tpred, na.rm = TRUE)*1000
  res$dev_mK[i]  <- (mean(Tpred, na.rm = TRUE) - T_sprt_mean)*1000
}

cat("Deviation of each NTC from expectation (last calibration), window mean:\n")
print(within(res, {
  T_ntc_C <- round(T_ntc_C, 4)
  sd_mK   <- round(sd_mK, 2)
  dev_mK  <- round(dev_mK, 2)
}), row.names = FALSE)

cat(sprintf("\nMean |deviation| = %.1f mK   (range %.1f .. %.1f mK)\n",
            mean(abs(res$dev_mK)), min(res$dev_mK), max(res$dev_mK)))

## ---- 6) Time-matched residuals (removes bath drift) ----
## match each NTC sample to nearest SPRT sample, then T_ntc - T_sprt pointwise
match_sprt <- function(t) {
  idx <- findInterval(as.numeric(t), as.numeric(win_sprt$timestamp))
  idx <- pmin(pmax(idx, 1L), nrow(win_sprt))
  win_sprt$TSPRT_C[idx]
}
Tsprt_at_ntc <- match_sprt(win_ntc$DateTimePC)
cat("\nTime-matched residuals (T_ntc - T_sprt, bath drift removed):\n")
res2 <- data.frame(NTC = ntc_cols, bias_mK = NA_real_, sd_mK = NA_real_)
for (i in seq_along(ntc_cols)) {
  nm    <- ntc_cols[i]
  Tpred <- S4_predict_T_C(NTCcounts2R(win_ntc[[nm]]), get_coef(nm))
  r     <- (Tpred - Tsprt_at_ntc) * 1000
  res2$bias_mK[i] <- mean(r, na.rm = TRUE)
  res2$sd_mK[i]   <- sd(r,   na.rm = TRUE)
}
print(within(res2, { bias_mK <- round(bias_mK, 2); sd_mK <- round(sd_mK, 2) }),
      row.names = FALSE)

## ---- 7) Diagnostic PDF: matched residuals over time ----
pdf("ntc2test_deviation.pdf", width = 9, height = 6)
cols <- c("#1b9e77","#d95f02","#7570b3","#e7298a","#66a61e","#e6ab02")
tmin <- as.numeric(win_ntc$DateTimePC - t0, units = "mins")
plot(NA, xlim = range(tmin), ylim = c(-45, 20),
     xlab = "Time since start (min)", ylab = "T_NTC - T_SPRT (mK)",
     main = "ntc2test: deviation from last calibration (bath ~ -20 C)")
abline(h = 0, lty = 2, col = "grey40")
for (i in seq_along(ntc_cols)) {
  nm    <- ntc_cols[i]
  Tpred <- S4_predict_T_C(NTCcounts2R(win_ntc[[nm]]), get_coef(nm))
  points(tmin, (Tpred - Tsprt_at_ntc)*1000, pch = 16, cex = .4, col = cols[i])
  abline(h = res2$bias_mK[i], col = cols[i], lwd = 2)          # mean deviation
}
legend("bottomleft",
       legend = sprintf("%s  (%+.1f mK)", ntc_cols, res2$bias_mK),
       col = cols, lwd = 2, pch = 16, bty = "n", cex = 0.9)
dev.off()
cat("\nWrote ntc2test_deviation.pdf\n")

## ---- 8) Full absolute temperature course (whole run) ----
pdf("ntc2test_temperature.pdf", width = 9, height = 6)
tmin_all  <- as.numeric(df_ntc$DateTimePC  - t0, units = "mins")
tmin_sprt <- as.numeric(df_sprt$timestamp  - t0, units = "mins")
## y-range from all series
Tntc_all <- sapply(ntc_cols, function(nm)
  S4_predict_T_C(NTCcounts2R(df_ntc[[nm]]), get_coef(nm)))
yr <- range(c(df_sprt$TSPRT_C, Tntc_all), na.rm = TRUE)
plot(tmin_sprt, df_sprt$TSPRT_C, type = "l", lwd = 2, col = "black",
     xlim = range(c(tmin_all, tmin_sprt)), ylim = yr,
     xlab = "Time since start (min)", ylab = "Temperature (C)",
     main = "ntc2test: absolute temperature course (SPRT + NTCs)")
for (i in seq_along(ntc_cols))
  lines(tmin_all, Tntc_all[, i], col = cols[i], lwd = 1)
abline(v = trim_start_min, lty = 3, col = "grey50")   # start of eval window
legend("topright",
       legend = c("SPRT (reference)", ntc_cols),
       col    = c("black", cols),
       lwd    = c(2, rep(1, length(ntc_cols))), bty = "n", cex = 0.85)
text(trim_start_min, yr[2], "eval window ->", pos = 4, cex = 0.7, col = "grey40")
dev.off()
cat("Wrote ntc2test_temperature.pdf\n")
