##' @title Source all .R files in a directory
##' @description Recursively loads all R scripts from a given folder.
##'
##' @param path Character. Directory containing .R files.
##' @param recursive Logical. If TRUE, also source files from subdirectories.
##' @param trace Logical. Print which files are sourced.
##'
##' @return Invisibly returns vector of sourced files.
##'
##' @examples
##' sourceDir("lib")   # loads all scripts in ./lib/
##' sourceDir("R", recursive = TRUE)
sourceDir <- function(path, recursive = FALSE, trace = TRUE) {
  if (!dir.exists(path)) {
    stop("Directory does not exist: ", path)
  }
  
  files <- list.files(
    path,
    pattern = "\\.R$",
    full.names = TRUE,
    recursive = recursive
  )
  
  if (length(files) == 0) {
    warning("No .R files found in directory: ", path)
    return(invisible(character(0)))
  }
  
  for (f in files) {
    if (trace) message("Sourcing: ", f)
    sys.source(f, envir = .GlobalEnv)
  }
  
  invisible(files)
}