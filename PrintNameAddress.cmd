@echo off
setlocal
start "" "%~dp0runtime\pythonw.exe" -E -s -B "%~dp0PrintNameAddress.pyw"
