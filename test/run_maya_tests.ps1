# Run Maya Tests via Command Port
# ================================
# 
# This script runs mayatk unit tests in Maya via command port
#
# Prerequisites:
#   1. Maya must be running
#   2. Command port must be open (run setup_maya_for_tests.py in Maya)
#
# Usage:
#   .\run_maya_tests.ps1                          # Run all tests
#   .\run_maya_tests.ps1 core_utils_test.py      # Run specific test
#   .\run_maya_tests.ps1 -Port 7003               # Use custom port

param(
    [Parameter(Position=0, ValueFromRemainingArguments=$true)]
    [string[]]$TestFiles,
    
    [Parameter()]
    [string]$Host = "localhost",
    
    [Parameter()]
    [int]$Port = 7002
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runnerScript = Join-Path $scriptDir "maya_test_runner.py"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Maya Test Runner" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Build command
$cmd = "python `"$runnerScript`" --host $Host --port $Port"

if ($TestFiles.Count -gt 0) {
    $cmd += " " + ($TestFiles -join " ")
    Write-Host "Running specific tests: $($TestFiles -join ', ')" -ForegroundColor Yellow
} else {
    Write-Host "Running all tests" -ForegroundColor Yellow
}

Write-Host "Connecting to Maya at ${Host}:${Port}" -ForegroundColor Yellow
Write-Host ""

# Execute
Invoke-Expression $cmd

# Exit with test result code
exit $LASTEXITCODE
