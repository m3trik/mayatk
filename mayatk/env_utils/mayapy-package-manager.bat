@ECHO off
SETLOCAL EnableDelayedExpansion
:: Maya Python Package Manager for Windows
:: Manages Python packages for Maya's bundled Python interpreter (mayapy).
CHCP 65001 >nul 2>&1
MODE CON: COLS=80 LINES=35

:intro
cls
color 0F
set "ver=2.0.0"
ECHO.
powershell -Command "$h='╔══════════════════════════════════════════════════════════════════════════╗';$m='║                    MAYA PYTHON PACKAGE MANAGER                           ║';$v='║                           v%ver%                                          ║';$f='╚══════════════════════════════════════════════════════════════════════════╝';Write-Host $h -ForegroundColor DarkCyan;Write-Host $m -ForegroundColor Gray;Write-Host $v -ForegroundColor DarkGray;Write-Host $f -ForegroundColor DarkCyan"
ECHO.

:setVersion
:: Auto-detect Maya versions
set "found_versions="
set "latest_version="
for %%Y in (2022 2023 2024 2025 2026) do (
    if exist "%ProgramFiles%\Autodesk\Maya%%Y\bin\mayapy.exe" (
        set "found_versions=!found_versions! %%Y"
        set "latest_version=%%Y"
    )
)

if defined found_versions (
    powershell -Command "Write-Host '  [OK] Detected Maya installations:' -ForegroundColor DarkGreen -NoNewline; Write-Host '%found_versions%' -ForegroundColor DarkYellow"
) else (
    powershell -Command "Write-Host '  [!!] No Maya installations detected in default location' -ForegroundColor DarkRed"
)

ECHO.
if defined latest_version (
    powershell -Command "Write-Host '  Enter Maya version [%latest_version%]: ' -ForegroundColor Gray -NoNewline"
    set "maya_version="
    set /p "maya_version="
    if not defined maya_version set "maya_version=%latest_version%"
) else (
    powershell -Command "Write-Host '  Enter Maya version: ' -ForegroundColor Gray -NoNewline"
    set /p "maya_version="
)
set "mayapy=%ProgramFiles%\Autodesk\Maya%maya_version%\bin\mayapy.exe"
goto validateMayapyPath

:validateMayapyPath
IF EXIST "%mayapy%" (
    powershell -Command "Write-Host '  [OK] ' -ForegroundColor DarkGreen -NoNewline; Write-Host 'Maya %maya_version% Python interpreter ready' -ForegroundColor Gray"
    :: Verify pip is available
    "%mayapy%" -m pip --version >nul 2>&1
    IF ERRORLEVEL 1 (
        powershell -Command "Write-Host '  [..] Installing pip...' -ForegroundColor DarkYellow"
        "%mayapy%" -m ensurepip --upgrade >nul 2>&1
    )
    timeout /t 1 >nul
    goto main
) ELSE (
    powershell -Command "Write-Host '  [!!] Maya %maya_version% not found' -ForegroundColor DarkRed"
    ECHO.
    powershell -Command "Write-Host '  Enter full path to mayapy.exe: ' -ForegroundColor Gray -NoNewline"
    set /p "mayapy="
    goto validateMayapyPath
)


:main
cls
ECHO.
powershell -Command "$t='  ┌─────────────────────────────────────────────────────────────────────────┐';$b='  └─────────────────────────────────────────────────────────────────────────┘';Write-Host $t -ForegroundColor DarkGray;Write-Host '  │  MAYA %maya_version% PACKAGE MANAGER                                            │' -ForegroundColor Gray;Write-Host $b -ForegroundColor DarkGray"
ECHO.
powershell -Command "Write-Host '     [1]  Install Package' -ForegroundColor Gray"
powershell -Command "Write-Host '     [2]  Update Package' -ForegroundColor Gray"
powershell -Command "Write-Host '     [3]  Uninstall Package' -ForegroundColor Gray"
powershell -Command "Write-Host '     [4]  Show Package Info' -ForegroundColor Gray"
powershell -Command "Write-Host '     [5]  List Installed Packages' -ForegroundColor Gray"
powershell -Command "Write-Host '     [6]  Check Outdated Packages' -ForegroundColor Gray"
ECHO.
powershell -Command "Write-Host '     [7]  Backup to requirements.txt' -ForegroundColor DarkGray"
powershell -Command "Write-Host '     [8]  Restore from requirements.txt' -ForegroundColor DarkGray"
ECHO.
powershell -Command "Write-Host '     [9]  Run as Administrator' -ForegroundColor DarkYellow"
powershell -Command "Write-Host '     [0]  Exit' -ForegroundColor DarkRed"
ECHO.
powershell -Command "Write-Host '  ─────────────────────────────────────────────────────────────────────────' -ForegroundColor DarkGray"
powershell -Command "Write-Host '  Select option: ' -ForegroundColor DarkCyan -NoNewline"

CHOICE /C:1234567890 /N

IF ERRORLEVEL 10 goto end
IF ERRORLEVEL 9 goto admin
IF ERRORLEVEL 8 goto restore
IF ERRORLEVEL 7 goto backup
IF ERRORLEVEL 6 goto outdated
IF ERRORLEVEL 5 goto list
IF ERRORLEVEL 4 goto info
IF ERRORLEVEL 3 goto uninstall
IF ERRORLEVEL 2 goto update
IF ERRORLEVEL 1 goto install


:install
cls
call :header "INSTALL PACKAGE"
ECHO.
powershell -Command "Write-Host '  Package name (e.g., scipy or scipy==1.14.0): ' -ForegroundColor Gray -NoNewline"
set /p "module="
if "%module%"=="" goto main
ECHO.
powershell -Command "Write-Host '  [..] Installing %module%...' -ForegroundColor DarkYellow"
ECHO.
"%mayapy%" -m pip install %module%
call :result
goto main


:uninstall
cls
call :header "UNINSTALL PACKAGE"
ECHO.
powershell -Command "Write-Host '  Package name to remove: ' -ForegroundColor Gray -NoNewline"
set /p "module="
if "%module%"=="" goto main
ECHO.
powershell -Command "Write-Host '  [..] Removing %module%...' -ForegroundColor DarkYellow"
ECHO.
"%mayapy%" -m pip uninstall %module% -y
call :result
goto main


:list
cls
call :header "INSTALLED PACKAGES"
ECHO.
"%mayapy%" -m pip list --format=columns
call :result
goto main


:update
cls
call :header "UPDATE PACKAGE"
ECHO.
powershell -Command "Write-Host '  Package name (or ' -ForegroundColor Gray -NoNewline; Write-Host 'all' -ForegroundColor DarkYellow -NoNewline; Write-Host ' for everything): ' -ForegroundColor Gray -NoNewline"
set /p "module="
if "%module%"=="" goto main
ECHO.
if /I "%module%"=="all" (
    powershell -Command "Write-Host '  [..] Updating all packages...' -ForegroundColor DarkYellow"
    ECHO.
    for /f "skip=2 tokens=1" %%p in ('"%mayapy%" -m pip list --outdated 2^>nul') do (
        powershell -Command "Write-Host '      > Updating %%p' -ForegroundColor DarkGray"
        "%mayapy%" -m pip install --upgrade %%p >nul 2>&1
    )
    powershell -Command "Write-Host '  [OK] All packages updated!' -ForegroundColor DarkGreen"
) else (
    powershell -Command "Write-Host '  [..] Updating %module%...' -ForegroundColor DarkYellow"
    ECHO.
    "%mayapy%" -m pip install %module% --upgrade
)
call :result
goto main


:info
cls
call :header "PACKAGE INFO"
ECHO.
powershell -Command "Write-Host '  Package name: ' -ForegroundColor Gray -NoNewline"
set /p "module="
if "%module%"=="" goto main
ECHO.
"%mayapy%" -m pip show %module%
call :result
goto main


:outdated
cls
call :header "OUTDATED PACKAGES"
ECHO.
powershell -Command "Write-Host '  [..] Checking for updates...' -ForegroundColor DarkYellow"
ECHO.
"%mayapy%" -m pip list --outdated --format=columns
call :result
goto main


:backup
cls
call :header "BACKUP PACKAGES"
set "backup_file=maya%maya_version%_requirements.txt"
ECHO.
powershell -Command "Write-Host '  [..] Creating backup...' -ForegroundColor DarkYellow"
"%mayapy%" -m pip freeze > "%backup_file%"
ECHO.
powershell -Command "Write-Host '  [OK] Saved: ' -ForegroundColor DarkGreen -NoNewline; Write-Host '%cd%\%backup_file%' -ForegroundColor Gray"
call :result
goto main


:restore
cls
call :header "RESTORE PACKAGES"
set "backup_file=maya%maya_version%_requirements.txt"
IF NOT EXIST "%backup_file%" (
    IF EXIST "requirements.txt" (
        set "backup_file=requirements.txt"
    ) ELSE (
        ECHO.
        powershell -Command "Write-Host '  [!!] No requirements file found' -ForegroundColor DarkRed"
        call :result
        goto main
    )
)
ECHO.
powershell -Command "Write-Host '  Packages in %backup_file%:' -ForegroundColor DarkCyan"
ECHO.
powershell -Command "Get-Content '%backup_file%' | ForEach-Object { Write-Host \"     $_\" -ForegroundColor DarkGray }"
ECHO.
powershell -Command "Write-Host '  Proceed with restore? [Y/N]: ' -ForegroundColor DarkYellow -NoNewline"
CHOICE /C:YN /N
IF ERRORLEVEL 2 (
    powershell -Command "Write-Host '  [--] Cancelled' -ForegroundColor DarkRed"
) ELSE (
    ECHO.
    powershell -Command "Write-Host '  [..] Restoring packages...' -ForegroundColor DarkYellow"
    ECHO.
    "%mayapy%" -m pip install -r "%backup_file%"
    ECHO.
    powershell -Command "Write-Host '  [OK] Restore complete!' -ForegroundColor DarkGreen"
)
call :result
goto main


:admin
powershell -Command "Write-Host '  [..] Requesting administrator privileges...' -ForegroundColor DarkYellow"
powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
goto end


:header
powershell -Command "Write-Host '' ; Write-Host '  ══════════════════════════════════════════════════════════════════════════' -ForegroundColor DarkGray; Write-Host '   %~1' -ForegroundColor Gray; Write-Host '  ══════════════════════════════════════════════════════════════════════════' -ForegroundColor DarkGray"
goto :eof


:result
ECHO.
powershell -Command "Write-Host '  ─────────────────────────────────────────────────────────────────────────' -ForegroundColor DarkGray"
powershell -Command "Write-Host '  Press any key to continue...' -ForegroundColor DarkGray"
pause >nul
goto :eof


:end
cls
ECHO.
powershell -Command "Write-Host '  Goodbye!' -ForegroundColor DarkCyan"
ECHO.
timeout /t 1 >nul
ENDLOCAL
exit /b 0
