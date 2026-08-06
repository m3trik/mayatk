@ECHO off
SETLOCAL EnableDelayedExpansion EnableExtensions
:: Maya Python Package Manager (thin wrapper) for Windows.
:: Detects Maya, resolves mayapy.exe, then hands off to the shared, interpreter-agnostic
:: package-manager.bat (m3trik\package-manager.bat) which owns the menu/operations.
:: Usage: mayapy-package-manager.bat [maya_version]
::   maya_version is optional; if omitted, auto-detects installs under
::   %ProgramFiles%\Autodesk\Maya* and prompts for one.
set "preselected_version=%~1"

:setVersion
:: Auto-detect Maya versions by scanning the default Autodesk install dir (the "scan strategy").
set "found_versions="
set "latest_version="
for /f "tokens=*" %%D in ('dir /b /ad "%ProgramFiles%\Autodesk\Maya*" 2^>nul') do (
    set "ver_str=%%D"
    set "ver_num=!ver_str:Maya=!"
    set "non_digit="
    for /f "delims=0123456789" %%X in ("!ver_num!") do set "non_digit=%%X"
    if not defined non_digit if exist "%ProgramFiles%\Autodesk\Maya!ver_num!\bin\mayapy.exe" (
        set "found_versions=!found_versions! !ver_num!"
        set "latest_version=!ver_num!"
    )
)

if defined found_versions (
    powershell -NoProfile -Command "Write-Host '  [OK] Detected Maya installations:' -ForegroundColor DarkGreen -NoNewline; Write-Host '%found_versions%' -ForegroundColor DarkYellow"
) else (
    powershell -NoProfile -Command "Write-Host '  [!!] No Maya installations detected in default location' -ForegroundColor DarkRed"
)

ECHO.
if defined preselected_version (
    set "maya_version=%preselected_version%"
    set "preselected_version="
) else if defined latest_version (
    powershell -NoProfile -Command "Write-Host '  Enter Maya version [%latest_version%]: ' -ForegroundColor Gray -NoNewline"
    set "maya_version="
    set /p "maya_version="
    if not defined maya_version set "maya_version=%latest_version%"
) else (
    powershell -NoProfile -Command "Write-Host '  Enter Maya version: ' -ForegroundColor Gray -NoNewline"
    set /p "maya_version="
)
set "mayapy=%ProgramFiles%\Autodesk\Maya%maya_version%\bin\mayapy.exe"

:validateMayapyPath
IF EXIST "%mayapy%" goto handoff
powershell -NoProfile -Command "Write-Host '  [!!] Maya %maya_version% not found' -ForegroundColor DarkRed"
ECHO.
powershell -NoProfile -Command "Write-Host '  Enter full path to mayapy.exe (blank to retry version): ' -ForegroundColor Gray -NoNewline"
set "mayapy="
set /p "mayapy="
if not defined mayapy goto setVersion
goto validateMayapyPath

:handoff
:: Locate the shared menu: alongside this wrapper (distributed), in the monorepo
:: (m3trik), or — when this wrapper was downloaded as a single file — fetched from
:: the repo mirror, so one download is enough to bootstrap (curl ships with Win10+).
set "generic=%~dp0package-manager.bat"
if not exist "%generic%" set "generic=%~dp0..\..\..\m3trik\package-manager.bat"
if not exist "%generic%" (
    powershell -NoProfile -Command "Write-Host '  [..] Fetching shared package-manager.bat' -ForegroundColor Gray"
    curl -fsSL -o "%~dp0package-manager.bat" "https://raw.githubusercontent.com/m3trik/mayatk/master/mayatk/env_utils/package-manager.bat" 2>nul
    REM cmd.exe mis-scans LF-only bats - normalize whatever the endpoint served.
    if exist "%~dp0package-manager.bat" powershell -NoProfile -Command "$p = '%~dp0package-manager.bat'; $t = [IO.File]::ReadAllText($p); [IO.File]::WriteAllText($p, ($t -replace '\r', '' -replace '\n', ([char]13 + [char]10)))"
    set "generic=%~dp0package-manager.bat"
)
IF NOT EXIST "%generic%" (
    powershell -NoProfile -Command "Write-Host '  [!!] Shared package-manager.bat not found next to this wrapper or in m3trik, and it could not be downloaded.' -ForegroundColor DarkRed"
    timeout /t 3 >nul
    ENDLOCAL
    exit /b 1
)
call "%generic%" "%mayapy%" "Maya %maya_version%" "maya%maya_version%"
ENDLOCAL
exit /b %ERRORLEVEL%
