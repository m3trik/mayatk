# =============================================================================
# MAYATK TEST RUNNER
# =============================================================================
# Thin PowerShell wrapper for run_tests.py
# Provides Windows-friendly commands and output handling
#
# Usage:
#   .\test.ps1                    # Run default core tests
#   .\test.ps1 -List              # List available test modules
#   .\test.ps1 -Quick             # Run quick validation test
#   .\test.ps1 -All               # Run ALL test modules
#   .\test.ps1 core node          # Run specific modules
#   .\test.ps1 -DryRun            # Validate without running
#   .\test.ps1 -Results           # View last test results
#   .\test.ps1 -Watch             # Monitor test results in real-time
#   .\test.ps1 -Connect           # Test Maya connection
# =============================================================================

[CmdletBinding(DefaultParameterSetName='Run')]
param(
    [Parameter(ParameterSetName='Run', Position=0, ValueFromRemainingArguments=$true)]
    [string[]]$Modules,
    
    [Parameter(ParameterSetName='List')]
    [switch]$List,
    
    [Parameter(ParameterSetName='Quick')]
    [switch]$Quick,
    
    [Parameter(ParameterSetName='All')]
    [switch]$All,
    
    [Parameter(ParameterSetName='Run')]
    [switch]$DryRun,
    
    [Parameter(ParameterSetName='Results')]
    [switch]$Results,
    
    [Parameter(ParameterSetName='Watch')]
    [switch]$Watch,
    
    [Parameter(ParameterSetName='Connect')]
    [switch]$Connect,
    
    [Parameter()]
    [switch]$Help
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Show help if requested
if ($Help) {
    # Just call Python help and add Windows-specific tips
    python "$ScriptDir\run_tests.py" --help
    Write-Host ""
    Write-Host "Windows PowerShell Commands:" -ForegroundColor Cyan
    Write-Host "  .\test.ps1 -Results     View last test results" -ForegroundColor Gray
    Write-Host "  .\test.ps1 -Watch       Monitor test results in real-time" -ForegroundColor Gray
    Write-Host "  .\test.ps1 -Connect     Test Maya connection" -ForegroundColor Gray
    Write-Host ""
    exit 0
}

# Handle -Connect
if ($Connect) {
    Write-Host "`nTesting Maya connection..." -ForegroundColor Cyan
    python "$ScriptDir\maya_connection.py"
    exit $LASTEXITCODE
}

# Handle -Results (Windows-specific feature)
if ($Results) {
    $resultsFile = Join-Path $ScriptDir "test_results.txt"
    if (Test-Path $resultsFile) {
        Write-Host "`nTest Results:" -ForegroundColor Cyan
        Write-Host ("=" * 70) -ForegroundColor Gray
        Get-Content $resultsFile
        Write-Host ("=" * 70) -ForegroundColor Gray
    } else {
        Write-Host "`n✗ No results file found. Run tests first." -ForegroundColor Red
        exit 1
    }
    exit 0
}

# Handle -Watch (Windows-specific feature)
if ($Watch) {
    $resultsFile = Join-Path $ScriptDir "test_results.txt"
    Write-Host "`nMonitoring test results (Ctrl+C to stop)..." -ForegroundColor Cyan
    Write-Host "File: $resultsFile`n" -ForegroundColor Gray
    
    if (Test-Path $resultsFile) {
        Get-Content $resultsFile -Wait
    } else {
        Write-Host "Waiting for results file to be created..." -ForegroundColor Yellow
        while (!(Test-Path $resultsFile)) {
            Start-Sleep -Seconds 1
        }
        Get-Content $resultsFile -Wait
    }
    exit 0
}

# Build Python command arguments
$pythonArgs = @()

if ($List) {
    $pythonArgs += "--list"
}
elseif ($Quick) {
    $pythonArgs += "--quick"
}
elseif ($All) {
    $pythonArgs += "--all"
}
elseif ($DryRun) {
    $pythonArgs += "--dry-run"
    if ($Modules) {
        $pythonArgs += $Modules
    }
}
elseif ($Modules) {
    $pythonArgs += $Modules
}

# Run the Python test runner
Write-Host ""
python "$ScriptDir\run_tests.py" @pythonArgs
$exitCode = $LASTEXITCODE

# Show helpful tip if tests completed
if ($exitCode -eq 0 -and !$List -and !$Quick -and !$DryRun) {
    Write-Host "`nTip: Use '.\test.ps1 -Results' to view full results" -ForegroundColor DarkGray
    Write-Host "     Use '.\test.ps1 -Watch' to monitor in real-time`n" -ForegroundColor DarkGray
}

exit $exitCode
