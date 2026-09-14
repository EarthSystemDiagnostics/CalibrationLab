library(ggplot2)
library(readxl)
library(zoo)
library(PaleoSpec)
library(tidyr)
library(dplyr)
library(readr)
library(pracma)
library(stringr)
library(plotly)


# Read NTC File and Correct for Ground

#readout = 0 : only readout of NTCs and GND; k_GND running mean window for GND correction
GetHeadData_GNDCorr <- function(filepath, readout=0, k_GND = 11){
  df_head_raw<-GetPartialHeadData(filepath) 

  df_head <- df_head_raw %>%
    mutate(across(contains("GND"),~ na.approx(.x, na.rm=FALSE, xout = seq_along(.x)))) %>%
    mutate(across(contains("GND"),~ na.locf(.x, na.rm = FALSE))) %>%
    mutate(across(contains("GND"),~ na.locf(.x, na.rm=FALSE, fromLast = TRUE)))
  df_head <- RemoveGND(df_head,readout = readout,k_GND=k_GND)
  df_head_NA <- df_head
  df_head <- df_head[complete.cases(df_head),]
  df_head$DateTimePC <- as.POSIXct(df_head$DateTimePC,
                                   format = "%Y-%m-%d %H:%M:%OS",
                                   tz     = "UTC")
  # Achtung TimeZone war nicht wirklich UTC, sondern deutsche Zeit!! Aber Hauptsache einheitlich bei SPRT und NTC
  
  return(df_head)
  
}