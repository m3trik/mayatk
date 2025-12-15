@echo off
REM =============================================================================
REM MAYATK TEST RUNNER (Batch Wrapper)
REM =============================================================================
REM Simple wrapper for PowerShell test script
REM Usage: test.bat [options]
REM =============================================================================

powershell.exe -ExecutionPolicy Bypass -File "%~dp0test.ps1" %*
