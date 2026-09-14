

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

#source("SPRTRtoT.R")

##################


#filename_head <- `20251028.115811TestHead_prelim`

readout= 3 # 3 = all resistors, 2 = only add. TestSB, 1 = only add. TestN, 0 = only ground and NTCs

#gets the data from the data file
GetDataHead <- function(filename, throw_values = 0){
  idx_empty <- which(filename[,]=="", arr.ind = TRUE)
  counts_raw <- filename[,]
  counts_raw[idx_empty] <-NA
  counts_raw <- counts_raw[complete.cases(counts_raw),]
  
  counts <- counts_raw
  counts <- counts %>%
    mutate(across(!DateTimePC,as.numeric))
  counts <- counts[complete.cases(counts),]
  counts <- counts[throw_values:length(counts[[1]]),]
  counts <- as.data.frame(counts)
  
  return(counts)
}


GetPartialHeadData <- function(filepath){
  counts_raw <- read_delim(filepath, delim=";", trim_ws=TRUE)
  header <- names(counts_raw)
  startnode <- header[3]
  
  idx_chr <- which(counts_raw[,1]=="SecondsElapsed")
  counts_raw <- counts_raw[-idx_chr,]
  
  idx_chr2 <- which(counts_raw[,3]==startnode)
  counts_raw <- counts_raw[-idx_chr2,]
  counts_raw <- counts_raw%>%
    mutate(across(!DateTimePC,as.numeric))
  
  return(counts_raw)
}

# 
# idx_empty <- which(filename_head[,]=="", arr.ind = TRUE)
# counts_m40_raw <- `20251028.115811TestHead_prelim`[,]
# counts_m40_raw[idx_empty] <-NA
# counts_m40_raw <- counts_m40_raw[complete.cases(counts_m40_raw),]
# 
# TestObject <- as.data.frame(lapply(counts_m40_raw[-2], as.numeric))
# TestObject <- TestObject[complete.cases(TestObject),]
# counts_m40_N04_GND <- as.integer(counts_m40_raw$N04_GND)
# 
# counts_m40 <- counts_m40_raw
# counts_m40$N04_GND <- counts_m40_N04_GND
# counts_m40 <- counts_m40[complete.cases(counts_m40),]
# #counts_m40 <- counts_m40[48:length(counts_m40),]# hier vllt. noch elegantere Loesung finden
# #counts_m40 <- lapply(counts_m40,as.numeric)
# counts_m40 <- counts_m40 %>%
#   mutate(across(!DateTimePC,as.numeric))
# counts_m40_uncorrectedNTCs <- counts_m40[c(5,6,10,11,15,16,20,21)]
# counts_m40[c(5,6,10,11,15,16,20,21)] <- as.data.frame(counts_m40[c(5,6,10,11,15,16,20,21)]) - as.data.frame(counts_m40[c(9,9,14,14,19,19,24,24)])

# gives the number of SENSOR BOARDS (i.e. half the number of NTCs)
GetNrSensors <- function(data){
  sensor_colnames <- as.data.frame(data[]) %>%
    select(contains("NTC")) %>%
    names()
  nr_sensors <- length(sensor_colnames)/2
  return(nr_sensors)
}

#corrects for the ground
RemoveGND <- function(data,readout=3,k_GND=11){
  countsGNDremoved <- as.data.frame(data[])
  GND_data <- countsGNDremoved%>%
    select(contains("GND"))
  l <- length(countsGNDremoved[[1]])
  n <- GetNrSensors(data)
  ValueGND_extendleft <- (5*GND_data[1,c(1:n)]+ 4*GND_data[2,c(1:n)]+3*GND_data[3,c(1:n)]+2*GND_data[4,c(1:n)]+GND_data[5,c(1:n)])/15
  ValueGND_extendright <- (5*GND_data[l,c(1:n)]+ 4*GND_data[l-1,c(1:n)]+3*GND_data[l-2,c(1:n)]+2*GND_data[l-3,c(1:n)]+GND_data[l-4,c(1:n)])/15
  GND_extendleft <- data.frame(repmat(0,(k_GND-1)/2,n))
  GND_extendright <- data.frame(repmat(0,(k_GND-1)/2,n))
  for (i in c(1:n)) {
    GND_extendleft[i] <- rep(ValueGND_extendleft[1,i],(k_GND-1)/2)
    GND_extendright[i] <- rep(ValueGND_extendright[1,i],(k_GND-1)/2)
  }
  GND_extendleft <- as.data.frame(GND_extendleft)
  GND_extendright <- as.data.frame(GND_extendright)
  colnames(GND_extendleft) <- colnames(GND_data)
  colnames(GND_extendright) <- colnames(GND_data)
  idx_sensor <- as.numeric(str_sub(colnames(GND_data),2,3))
  GND_extended <- bind_rows(GND_extendleft,GND_data,GND_extendright)
  GND_runmean <- rollmean(GND_extended[],k=k_GND)
  GND_runmean <- GND_runmean[c(((k_GND+1)/2):(l-(k_GND-1)/2))]
  for (i in c(1:n)) {
    countsGNDremoved <- countsGNDremoved %>%
      mutate(across(contains(paste0(idx_sensor[i],"_NTC")),~.x - GND_runmean[[i]]))
    if(readout == 1){
      countsGNDremoved <- countsGNDremoved %>%
        mutate(across(contains(paste0(idx_sensor[i],"_TestN")),~.x - GND_runmean[[i]]))
    }
    if(readout==2){
      countsGNDremoved <- countsGNDremoved %>%
        mutate(across(contains(paste0(idx_sensor[i],"_TestSB")),~.x - GND_runmean[[i]]))
    }
    if(readout==3){
      countsGNDremoved <- countsGNDremoved %>%
        mutate(across(contains(paste0(idx_sensor[i],"_TestN")),~.x - GND_runmean[[i]])) %>%
        mutate(across(contains(paste0(idx_sensor[i],"_TestSB")),~.x - GND_runmean[[i]]))
    }
  }
  return(countsGNDremoved)
}


# k_GND <- 11
# TestObject2 <- RemoveGND(TestObject,readout=3,k_GND=11)
# l <- 
# TestGND_data <- TestObject2 %>%
#   select(contains("GND"))
# ValueGND_extendleft <- (5*TestGND_data[1,c(1:4)]+ 4*TestGND_data[2,c(1:4)]+3*TestGND_data[3,c(1:4)]+2*TestGND_data[4,c(1:4)]+TestGND_data[5,c(1:4)])/15
# TestGND_extendleft <- data.frame(repmat(0,5,4))
# for (i in c(1:4)) {
#   TestGND_extendleft[i] <- rep(ValueGND_extendleft[1,i],(k_GND-1)/2)
# }
# TestGND_extendleft <- as.data.frame(TestGND_extendleft)
# colnames(TestGND_extendleft) <- colnames(TestGND_data)
# Test_GND_extended <- bind_rows(TestGND_extendleft,TestGND_data)
# for (i in c(1:4)) {
#   TestObject2 <- TestObject2%>%
#     mutate(across(contains(paste0(i,"_NTC")),~.x - TestGND_data[,i]))
# }

####################


#gets datetime format right and filters for time frames
AdjustDatetime <- function(data,filter=FALSE,starttime="2025-12-12 8:00:00", endtime="2025-12-12 20:00:00"){
  counts_datetime_adjusted <- data %>%
    mutate(time = SecondsElapsed) %>% 
    mutate(
      DateTime_ct =  as.POSIXct(str_sub(DateTimePC,1,-8))
    ) %>% 
    select(time, DateTimeHead, DateTimePC, DateTime_ct, everything())
  
  if(filter==TRUE){
    startfilter <- as.POSIXct(starttime)
    endfilter <- as.POSIXct(endtime)
    counts_datetime_adjusted <- counts_datetime_adjusted %>%
      filter(DateTime_ct > startfilter & DateTime_ct < endfilter)
  }
  return(counts_datetime_adjusted)
}

#TestObject3 <- AdjustDatetime(count_dat,filter=TRUE,starttime = "2025-11-12 13:00:00", endtime = "2025-11-12 13:17:00")

##################


# from old code, calculates resistances from NTC counts
NTC2Res <- function(NTCcounts = c(6537064, 6537035, 6537060)){
  Rs = 1E6 #Ohm
  Rp = 499E3 #Ohm.  
  1/(1/5)
  temp = ((2^25/NTCcounts -1)/Rs - 1/Rp)
  return(1/temp)
}

###############


GetCalibMeans <- function(data,temp_data,NrSensors=5,NrPlateaus=6,idx_P,idx_SPRT){
  Nr_NTCs <- 2*NrSensors
  CalibDataNTCs <- data
  colnamesNTCs <- names(CalibDataNTCs)
  Calib_means_NTC <- as.data.frame(matrix(NA, nrow = NrPlateaus, ncol = Nr_NTCs))
  colnames(Calib_means_NTC)<-colnamesNTCs
  Calib_std_NTC <- as.data.frame(matrix(NA, nrow = NrPlateaus, ncol = Nr_NTCs))
  colnames(Calib_std_NTC)<-colnamesNTCs
  Calib_means_SPRT <- as.data.frame(matrix(NA, nrow = NrPlateaus, ncol=1))
  colnames(Calib_means_SPRT)<-"T_SPRT_C"
  Calib_std_SPRT <- as.data.frame(matrix(NA, nrow = NrPlateaus, ncol=1))
  colnames(Calib_std_SPRT)<-"T_SPRT_C"
  for (p in c(1:NrPlateaus)) {
    for (n in c(1:Nr_NTCs)) {
      Calib_means_NTC[p,n] <- mean(na.trim(CalibDataNTCs[idx_P[[p]],n]))
      Calib_std_NTC[p,n] <- std(na.trim(CalibDataNTCs[idx_P[[p]],n]))
    }
    Calib_means_SPRT[p,1] <- mean(na.trim(temp_data[idx_SPRT[[p]]]))
    Calib_std_SPRT[p,1] <- std(na.trim(temp_data[idx_SPRT[[p]]]))
  }
  Calib_means <- list(Calib_means_NTC =Calib_means_NTC,Calib_std_NTC = Calib_std_NTC,Calib_means_SPRT=Calib_means_SPRT,Calib_std_SPRT=Calib_std_SPRT)
}

#####################


#Fit Formula
fit.S4 <- function(T,R)
{
  iT <- 1/T
  lR <- log(R)
  lR2 <- lR^2
  lR3 <- lR^3
  return(lm(iT~lR+lR2+lR3)$coeff)
  
}
#######################


#formula to retreive temperature from sensor resistances
predict.S4 <- function(model,R)
{
  lR <- log(R)
  # lR2 <- lR^2
  # lR3 <- lR^3
  return(1/(model[1]+model[2]*lR+model[3]*lR^2+model[4]*lR^3))
}


RemoveSimpleGND <- function(df_raw){
  idx_GND <- which(grepl("GND", colnames(df_raw)))
  idx_GND <- rep(idx_GND, each = 2)
  idx_NTC <- which(grepl("NTC", colnames(df_raw)))
  df_raw[,idx_NTC]<-df_raw[,idx_NTC]-df_raw[,idx_GND]
  
  return(df_raw)
}



GetCalibCoeff <- function(cal_project=cal_project){
  results_calibS4 <-c()
  coeffS4_list <-data.frame(matrix(ncol = sum(sapply(cal_project$heads, function(x)
    length(x$cal_all$calib_list))),nrow=4))
  colnames_calib <- unlist(
    lapply(cal_project$heads, function(x)
      as.character(names(x$cal_all$calib_list))),
    use.names = FALSE
  )
  rownames_calib <-names(cal_project$heads[[1]]$cal_all$calib_list[[1]]$coef_S4)
  colnames(coeffS4_list) <- colnames_calib
  rownames(coeffS4_list) <- rownames_calib
  k=1
  for (i in c(1:length(cal_project$heads))) {
    results_calibS4[[i]] <- cal_project$heads[[i]]$cal_all$calib_list
    
    for (j in c(1:length(results_calibS4[[i]]))) {
      
      coeffS4 <- results_calibS4[[i]][[j]]$coef_S4
      coeffS4_list[k]<-coeffS4
      k<-k+1
      
    }
  }
  return(coeffS4_list)
}
