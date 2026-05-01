@ECHO off
SETLOCAL EnableDelayedExpansion EnableExtensions
:: Maya Python Package Manager for Windows
:: Manages Python packages for Maya's bundled Python interpreter (mayapy).
:: Usage: mayapy-package-manager.bat [maya_version]
::   maya_version is optional; if omitted, the script auto-detects installs
::   under %ProgramFiles%\Autodesk\Maya* and prompts for one.
CHCP 65001 >nul 2>&1
MODE CON: COLS=80 LINES=35

set "ver=2.1.0"
set "PYTHONIOENCODING=utf-8"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "preselected_version=%~1"

:intro
cls
color 0F
ECHO.
powershell -NoProfile -Command "$w=76; $bg='DarkCyan'; $blank=' '*$w; $t='MAYA PYTHON PACKAGE MANAGER'; $v='v%ver%'; $tL=$t.PadLeft([int](($w-$t.Length)/2)+$t.Length).PadRight($w); $vL=$v.PadLeft([int](($w-$v.Length)/2)+$v.Length).PadRight($w); Write-Host $blank -BackgroundColor $bg; Write-Host $tL -BackgroundColor $bg -ForegroundColor White; Write-Host $vL -BackgroundColor $bg -ForegroundColor Gray; Write-Host $blank -BackgroundColor $bg"
ECHO.

:setVersion
:: Auto-detect Maya versions by scanning the default Autodesk install dir.
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
IF EXIST "%mayapy%" (
    powershell -NoProfile -Command "Write-Host '  [OK] ' -ForegroundColor DarkGreen -NoNewline; Write-Host 'Maya %maya_version% Python interpreter ready' -ForegroundColor Gray"
    "%mayapy%" -m pip --version >nul 2>&1
    IF ERRORLEVEL 1 (
        powershell -NoProfile -Command "Write-Host '  [..] Installing pip...' -ForegroundColor DarkYellow"
        "%mayapy%" -m ensurepip --upgrade >nul 2>&1
    )
    timeout /t 1 >nul
    goto main
) ELSE (
    powershell -NoProfile -Command "Write-Host '  [!!] Maya %maya_version% not found' -ForegroundColor DarkRed"
    ECHO.
    powershell -NoProfile -Command "Write-Host '  Enter full path to mayapy.exe (blank to retry version): ' -ForegroundColor Gray -NoNewline"
    set "mayapy="
    set /p "mayapy="
    if not defined mayapy goto setVersion
    goto validateMayapyPath
)


:main
cls
ECHO.
powershell -NoProfile -Command "Write-Host '  ┌─────────────────────────────────────────────────────────────────────────┐' -ForegroundColor DarkGray; Write-Host '  │  MAYA %maya_version% PACKAGE MANAGER                                            │' -ForegroundColor Gray; Write-Host '  └─────────────────────────────────────────────────────────────────────────┘' -ForegroundColor DarkGray; Write-Host ''; Write-Host '     [1]  Install Package' -ForegroundColor Gray; Write-Host '     [2]  Update Package' -ForegroundColor Gray; Write-Host '     [3]  Uninstall Package' -ForegroundColor Gray; Write-Host '     [4]  Show Package Info' -ForegroundColor Gray; Write-Host '     [5]  List Installed Packages' -ForegroundColor Gray; Write-Host '     [6]  Check Outdated Packages' -ForegroundColor Gray; Write-Host ''; Write-Host '     [7]  Backup to requirements.txt' -ForegroundColor DarkGray; Write-Host '     [8]  Restore from requirements.txt' -ForegroundColor DarkGray; Write-Host ''; Write-Host '     [9]  Run as Administrator' -ForegroundColor DarkYellow; Write-Host '     [0]  Exit' -ForegroundColor DarkRed; Write-Host ''; Write-Host '  ─────────────────────────────────────────────────────────────────────────' -ForegroundColor DarkGray; Write-Host '  Select option: ' -ForegroundColor DarkCyan -NoNewline"

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
goto main


:install
cls
call :header "INSTALL PACKAGE"
call :promptModule "Package name (e.g., scipy or scipy==1.14.0): "
if not defined module goto main
ECHO.
powershell -NoProfile -Command "Write-Host '  [..] Installing %module%...' -ForegroundColor DarkYellow"
ECHO.
"%mayapy%" -m pip install %module%
call :result
goto main


:uninstall
cls
call :header "UNINSTALL PACKAGE"
call :promptModule "Package name to remove: "
if not defined module goto main
ECHO.
powershell -NoProfile -Command "Write-Host '  [..] Removing %module%...' -ForegroundColor DarkYellow"
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
call :promptModule "Package name (or 'all' for everything): "
if not defined module goto main
ECHO.
if /I "%module%"=="all" (
    powershell -NoProfile -Command "Write-Host '  [..] Checking for outdated packages...' -ForegroundColor DarkYellow"
    set "pkg_list="
    for /f "skip=2 tokens=1 delims= " %%p in ('"%mayapy%" -m pip list --outdated --format=columns 2^>nul') do (
        set "pkg_list=!pkg_list! %%p"
    )
    if defined pkg_list (
        powershell -NoProfile -Command "Write-Host '  [..] Upgrading:!pkg_list!' -ForegroundColor DarkGray"
        ECHO.
        "%mayapy%" -m pip install --upgrade !pkg_list!
        ECHO.
        powershell -NoProfile -Command "Write-Host '  [OK] All packages updated' -ForegroundColor DarkGreen"
    ) else (
        powershell -NoProfile -Command "Write-Host '  [OK] All packages are up to date' -ForegroundColor DarkGreen"
    )
) else (
    powershell -NoProfile -Command "Write-Host '  [..] Updating %module%...' -ForegroundColor DarkYellow"
    ECHO.
    "%mayapy%" -m pip install %module% --upgrade
)
call :result
goto main


:info
cls
call :header "PACKAGE INFO"
call :promptModule "Package name: "
if not defined module goto main
ECHO.
"%mayapy%" -m pip show %module%
call :result
goto main


:outdated
cls
call :header "OUTDATED PACKAGES"
ECHO.
powershell -NoProfile -Command "Write-Host '  [..] Checking for updates...' -ForegroundColor DarkYellow"
ECHO.
"%mayapy%" -m pip list --outdated --format=columns
call :result
goto main


:backup
cls
call :header "BACKUP PACKAGES"
set "backup_file=maya%maya_version%_requirements.txt"
ECHO.
IF EXIST "%backup_file%" (
    powershell -NoProfile -Command "Write-Host '  [!!] %backup_file% already exists. Overwrite? [Y/N]: ' -ForegroundColor DarkYellow -NoNewline"
    CHOICE /C:YN /N
    :: Default to N so Ctrl+C / errorlevel 0 cancels rather than overwrites.
    set "ans=N"
    IF ERRORLEVEL 1 set "ans=Y"
    IF ERRORLEVEL 2 set "ans=N"
    IF /I "!ans!"=="N" (
        ECHO.
        powershell -NoProfile -Command "Write-Host '  [--] Cancelled' -ForegroundColor DarkRed"
        call :result
        goto main
    )
    ECHO.
)
powershell -NoProfile -Command "Write-Host '  [..] Creating backup...' -ForegroundColor DarkYellow"
"%mayapy%" -m pip freeze > "%backup_file%"
ECHO.
powershell -NoProfile -Command "Write-Host '  [OK] Saved: ' -ForegroundColor DarkGreen -NoNewline; Write-Host '%cd%\%backup_file%' -ForegroundColor Gray"
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
        powershell -NoProfile -Command "Write-Host '  [!!] No requirements file found' -ForegroundColor DarkRed"
        call :result
        goto main
    )
)
ECHO.
powershell -NoProfile -Command "Write-Host '  Packages in %backup_file%:' -ForegroundColor DarkCyan"
ECHO.
powershell -NoProfile -Command "Get-Content '%backup_file%' | ForEach-Object { Write-Host \"     $_\" -ForegroundColor DarkGray }"
ECHO.
powershell -NoProfile -Command "Write-Host '  Proceed with restore? [Y/N]: ' -ForegroundColor DarkYellow -NoNewline"
CHOICE /C:YN /N
:: Default to N so Ctrl+C / errorlevel 0 cancels rather than restoring.
set "ans=N"
IF ERRORLEVEL 1 set "ans=Y"
IF ERRORLEVEL 2 set "ans=N"
IF /I "%ans%"=="N" (
    ECHO.
    powershell -NoProfile -Command "Write-Host '  [--] Cancelled' -ForegroundColor DarkRed"
) ELSE (
    ECHO.
    powershell -NoProfile -Command "Write-Host '  [..] Restoring packages...' -ForegroundColor DarkYellow"
    ECHO.
    "%mayapy%" -m pip install -r "%backup_file%"
    ECHO.
    powershell -NoProfile -Command "Write-Host '  [OK] Restore complete' -ForegroundColor DarkGreen"
)
call :result
goto main


:admin
ECHO.
powershell -NoProfile -Command "Write-Host '  [..] Requesting administrator privileges...' -ForegroundColor DarkYellow"
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%maya_version%' -WorkingDirectory '%cd%' -Verb RunAs"
goto end


:promptModule
ECHO.
powershell -NoProfile -Command "Write-Host '  %~1' -ForegroundColor Gray -NoNewline"
set "module="
set /p "module="
goto :eof


:header
powershell -NoProfile -Command "Write-Host ''; Write-Host '  ══════════════════════════════════════════════════════════════════════════' -ForegroundColor DarkGray; Write-Host '   %~1' -ForegroundColor Gray; Write-Host '  ══════════════════════════════════════════════════════════════════════════' -ForegroundColor DarkGray"
goto :eof


:result
ECHO.
powershell -NoProfile -Command "Write-Host '  ─────────────────────────────────────────────────────────────────────────' -ForegroundColor DarkGray; Write-Host '  Press any key to continue...' -ForegroundColor DarkGray"
pause >nul
goto :eof


:end
cls
ECHO.
powershell -NoProfile -Command "Write-Host '  Goodbye!' -ForegroundColor DarkCyan"
ECHO.
timeout /t 1 >nul
ENDLOCAL
exit /b 0
