#Calibration functions for SPRT and NTC to T (raw) and NTC to R conversion
#No GND correction; NTC to R conversion just copied, unclear if this is complete

#SPRT Calibration values
#Kalibrationswerte 1. Halbjahr 2025:

SPRTglas_TWP <- 0.254210687 #0.254210813 (etwas später gemessen)
SPRT670SL_TWP <- 0.256828407 # later?:0.256822096 oder ist das ohne zero power?

SPRTglas_MERC <- 0.214602392 #is this the zero power value?
SPRT670SL_MERC <- 0.216812093 #is this the zero power value?

T_TWP <- 0.01 + 273.15
T_MERC <- -38.8344 + 273.15

#For glas SPRT:
x_SPRTglas <- c(SPRTglas_MERC,SPRTglas_TWP)
y_SPRTglas <- c(T_MERC,T_TWP)

m_SPRT_glas <- diff(y_SPRTglas)/diff(x_SPRTglas)

b_SPRT_glas <- y_SPRTglas[1]-m_SPRT_glas*x_SPRTglas[1]

SPRTglas_R2T <- function(x=c(0.254,0.255,0.256,0.257),m=m_SPRT_glas,b=b_SPRT_glas){
  y <- m*x+b
  return(y)
}

#For new SPRT 670SL
x_SPRT670SL <- c(SPRT670SL_MERC,SPRT670SL_TWP)
y_SPRT670SL <- c(T_MERC,T_TWP)

m_SPRT_670SL <- diff(y_SPRT670SL)/diff(x_SPRT670SL)

b_SPRT_670SL <- y_SPRT670SL[1]-m_SPRT_670SL*x_SPRT670SL[1]

SPRT670SL_R2T <- function(x=c(0.254,0.255,0.256,0.257),m=m_SPRT_670SL,b=b_SPRT_670SL){
  y <- m*x+b
  return(y)
}


NTCcounts2temp <- function(counts){
  H = (-counts * 1000000) / (counts - 33554432)
  resistance = (H * 499000) / (499000 - H)
  temperatures = 1 / ((1/3380) * log(resistance / 10000) + 1/298.15)
  temperatures = temperatures - 273.15
  return(temperatures)
}

NTCcounts2R <- function(counts){
  H = (-counts * 1000000) / (counts - 33554432)
  R= (H * 499000) / (499000 - H)
  return(R)
}




##' Fit a 4-parameter Steinhart–Hart–type thermistor model.
##'
##' This function fits a third-order polynomial model of the form:
##'
##' \deqn{ \frac{1}{T} = a + b \log(R) + c \log(R)^2 + d \log(R)^3 }
##'
##' where \eqn{T} is temperature in Kelvin and \eqn{R} is resistance in Ohm.
##'
##' The model is fit using linear regression of \eqn{1/T} on
##' \eqn{\log(R)}, \eqn{\log(R)^2}, and \eqn{\log(R)^3}.
##'
##' @param T Numeric vector of temperatures in Kelvin.
##' @param R Numeric vector of thermistor resistances in Ohm.
##'
##' @return A numeric vector of length 4 containing the fitted coefficients
##'   \eqn{(a, b, c, d)} corresponding to the Steinhart–Hart-type model.
##'
##' @examples
##' T <- c(250, 260, 270)  # K
##' R <- c(30000, 20000, 15000) # ohm
##' coef <- fit.S4(T, R)
##'
##' @author Thomas Laepple
fit.S4 <- function(T, R) {
  iT  <- 1 / T
  lR  <- log(R)
  lR2 <- lR^2
  lR3 <- lR^3
  lm(iT ~ lR + lR2 + lR3)$coefficients
}



##' Predict temperature in °C from resistance using a Steinhart–Hart 4-parameter fit.
##'
##' Given the coefficients \eqn{(a, b, c, d)} from the Steinhart–Hart-type model
##'
##' \deqn{ \frac{1}{T} = a + b\log(R) + c\log(R)^2 + d\log(R)^3 }
##'
##' this function computes the predicted temperature.
##'
##' @param R Numeric vector of thermistor resistances in Ohm.
##' @param coef_S4 Numeric vector of length 4 with the Steinhart coefficients
##'   \eqn{(a, b, c, d)} as produced by \code{fit.S4()}.
##'
##' @return Numeric vector of predicted temperatures in degrees Celsius.
##'
##' @examples
##' coef <- c(a = 1e-3, b = 2e-4, c = -1e-5, d = 5e-7)
##' R <- c(20000, 25000, 30000)
##' T_C <- S4_predict_T_C(R, coef)
##'
##' @author Thomas Laepple
S4_predict_T_C <- function(R, coef_S4) {
  a <- coef_S4[1]
  b <- coef_S4[2]
  c <- coef_S4[3]
  d <- coef_S4[4]
  
  lR  <- log(R)
  lR2 <- lR^2
  lR3 <- lR^3
  
  iT_hat  <- a + b*lR + c*lR2 + d*lR3  # 1/T in 1/K
  T_hat_K <- 1 / iT_hat
  T_hat_C <- T_hat_K - 273.15
  T_hat_C
}
