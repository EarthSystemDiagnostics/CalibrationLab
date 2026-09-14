## Reproducibility check: N94 & N96 (same as ntc2test) vs S4 calibration (SM2)
## This run: plateau at -14 C (run 20260703-160655).  ntc2test was at -20 C.

setwd("/Users/tlaepple/sharedAI/CalibrationChains")
source("./lib/sourceDir.R"); sourceDir("./lib", recursive = FALSE)
setwd("/Users/tlaepple/sharedAI/CalibrationChains/recalib2sensors")

read_logger_2026 <- function(file, tz = "UTC") {
  lines <- readLines(file); hdr <- strsplit(lines[1], ";")[[1]]
  ch <- trimws(strsplit(hdr[4], "\\|")[[1]]); ch <- ch[ch != ""]; ch <- sub("TestSB$", "NTC3", ch)
  keep <- grepl("[0-9]", lines) & grepl("\\|", lines) & !grepl("New Node Array", lines)
  pr <- function(ln) { f <- strsplit(ln, ";")[[1]]; ts <- trimws(f[3])
    vv <- suppressWarnings(as.numeric(trimws(strsplit(f[4], "\\|")[[1]]))); vv <- vv[!is.na(vv)]
    if (length(vv) != length(ch)) return(NULL); c(list(DateTimePC = ts), setNames(as.list(vv), ch)) }
  rows <- lapply(lines[keep], pr); rows <- rows[!sapply(rows, is.null)]
  df <- do.call(rbind.data.frame, c(rows, stringsAsFactors = FALSE))
  df$DateTimePC <- as.POSIXct(df$DateTimePC, format = "%Y-%m-%d %H:%M:%OS", tz = tz); df
}

df_ntc  <- read_logger_2026("Snowmelt_Retest5Sensors_20260703-160655_ntc.txt")
df_sprt <- read_microk_file("Snowmelt_Retest5Sensors_20260703-160655_microk.txt")
ntc_cols <- grep("NTC", names(df_ntc), value = TRUE)
old <- read.csv("../ntc2test/SM2_CalibrationCoefficients.csv", row.names = 1, check.names = FALSE)
oc  <- function(nm) as.numeric(old[c("a","b","c","d"), nm])

## plateau window from plateaus.txt: stable from 16:12:17
win_start <- as.POSIXct("2026-07-03 16:12:17", tz = "UTC")
sp_sel <- df_sprt$timestamp >= win_start
T14 <- mean(df_sprt$TSPRT[sp_sel] - 273.15)
cat(sprintf("-14 plateau: mean SPRT = %.3f C (sd %.1f mK, %d SPRT samples)\n\n",
            T14, sd(df_sprt$TSPRT[sp_sel]) * 1000, sum(sp_sel)))

rows <- lapply(ntc_cols, function(nm) {
  i  <- match(nm, ntc_cols)
  pr <- match_ntc_to_sprt(i, df_ntc, df_sprt)
  ok <- pr$time_ntc >= win_start & abs(pr$dt_sec) <= 5 & !is.na(pr$NTC_counts)
  Tp <- S4_predict_T_C(NTCcounts2R(pr$NTC_counts[ok]), oc(nm))
  res <- (Tp - pr$TSPRT_temp_C[ok]) * 1000
  data.frame(NTC = nm, n = sum(ok),
             bias_m14_mK = round(mean(res, na.rm = TRUE), 1),
             sd_m14_mK   = round(sd(res, na.rm = TRUE), 1))
})
now <- do.call(rbind, rows); rownames(now) <- NULL

## previous ntc2test result (at -20 C), from the first deviation test
ntc2test <- data.frame(
  NTC        = c("N94_NTC1","N94_NTC2","N94_NTC3","N96_NTC1","N96_NTC2","N96_NTC3"),
  bias_m20_ntc2test = c(-1.0, -1.6, -0.4, 1.5, -1.1, -29.9))

cmp <- merge(now, ntc2test, by = "NTC")
cmp <- cmp[order(cmp$NTC), c("NTC","n","bias_m14_mK","sd_m14_mK","bias_m20_ntc2test")]
cat("=== This run (-14 C) vs earlier ntc2test (-20 C), residual to SM2 [mK] ===\n")
print(cmp, row.names = FALSE)

write.csv(cmp, "Repro_vs_ntc2test.csv", row.names = FALSE)
cat("\nWrote Repro_vs_ntc2test.csv\n")
