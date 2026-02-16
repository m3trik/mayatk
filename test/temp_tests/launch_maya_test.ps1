# PowerShell script to launch Maya and verify Channel Box selection

$mayaPath = "C:\Program Files\Autodesk\Maya2025\bin\maya.exe"
if (-not (Test-Path $mayaPath)) {
    Write-Host "Maya 2025 not found, trying generic maya..."
    $mayaPath = "maya.exe"
}

$repoRoot = "O:/Cloud/Code/_scripts"
$scriptPath = "$repoRoot/mayatk/test/temp_tests/check_channel_box_selection.py"

# Construct Python commands
$pyCmd = "import sys; sys.path.append('$repoRoot'); import mayatk.test.temp_tests.check_channel_box_selection as t; t.test_channel_box_selection()"
$melCmd = "python(\`"$pyCmd\`")"

Write-Host "Launching Maya with test..."
& $mayaPath -command $melCmd
