@ECHO off
SETLOCAL EnableDelayedExpansion EnableExtensions
:: Generic Python Package Manager for Windows (interpreter-agnostic).
:: The shared menu/operations behind the thin per-DCC wrappers
:: (mayatk\env_utils\mayapy-package-manager.bat, blendertk\env_utils\blenderpy-package-manager.bat).
:: A wrapper detects its DCC, resolves the interpreter, and hands off here.
::
:: SSoT: this file lives in m3trik/. It is mirrored verbatim into each DCC package's
:: env_utils/ (so it ships in the wheel next to the wrapper) by
:: m3trik/scripts/sync_shared_bat.py -- edit HERE, never the mirror; run that script to propagate.
::
:: Usage: package-manager.bat "<python.exe>" "<Label>" "<backup_prefix>"
::   %1  Full path to the target Python interpreter (e.g. mayapy.exe / Blender's python.exe).
::   %2  Display label shown in the UI (e.g. "Maya 2025", "Blender 5.1").
::   %3  Prefix for the backup file (<prefix>_requirements.txt).
:: ASCII-only output (no box-drawing chars) so it is robust to the cmd UTF-8 codepage parsing bug.

set "ver=1.0.0"
set "PYTHONIOENCODING=utf-8"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"

set "interp=%~1"
set "label=%~2"
set "backup_prefix=%~3"
if not defined label set "label=Python"
if not defined backup_prefix set "backup_prefix=python"

:validateInterp
IF NOT EXIST "%interp%" (
    powershell -NoProfile -Command "Write-Host '  [!!] Interpreter not found: ' -ForegroundColor DarkRed -NoNewline; Write-Host '%interp%' -ForegroundColor Gray"
    powershell -NoProfile -Command "Write-Host '  Usage: package-manager.bat <python.exe> <Label> <backup_prefix>' -ForegroundColor DarkGray"
    timeout /t 3 >nul
    ENDLOCAL
    exit /b 1
)
"%interp%" -m pip --version >nul 2>&1
IF ERRORLEVEL 1 (
    powershell -NoProfile -Command "Write-Host '  [..] Installing pip...' -ForegroundColor DarkYellow"
    "%interp%" -m ensurepip --upgrade >nul 2>&1
)

:intro
cls
color 0F
ECHO.
powershell -NoProfile -Command "$w=76; $bg='DarkCyan'; $blank=' '*$w; $t=('%label% PACKAGE MANAGER').ToUpper(); $v='v%ver%'; $tL=$t.PadLeft([int](($w-$t.Length)/2)+$t.Length).PadRight($w); $vL=$v.PadLeft([int](($w-$v.Length)/2)+$v.Length).PadRight($w); Write-Host $blank -BackgroundColor $bg; Write-Host $tL -BackgroundColor $bg -ForegroundColor White; Write-Host $vL -BackgroundColor $bg -ForegroundColor Gray; Write-Host $blank -BackgroundColor $bg"
ECHO.
powershell -NoProfile -Command "Write-Host '  [OK] ' -ForegroundColor DarkGreen -NoNewline; Write-Host '%label% Python interpreter ready' -ForegroundColor Gray"
timeout /t 1 >nul
goto main


:main
cls
ECHO.
powershell -NoProfile -Command "Write-Host '  ===========================================================================' -ForegroundColor DarkGray; Write-Host ('   ' + ('%label% PACKAGE MANAGER')) -ForegroundColor Gray; Write-Host '  ===========================================================================' -ForegroundColor DarkGray; Write-Host ''; Write-Host '     [1]  Install Package' -ForegroundColor Gray; Write-Host '     [2]  Update Package' -ForegroundColor Gray; Write-Host '     [3]  Uninstall Package' -ForegroundColor Gray; Write-Host '     [4]  Show Package Info' -ForegroundColor Gray; Write-Host '     [5]  List Installed Packages' -ForegroundColor Gray; Write-Host '     [6]  Check Outdated Packages' -ForegroundColor Gray; Write-Host ''; Write-Host '     [7]  Backup to requirements.txt' -ForegroundColor DarkGray; Write-Host '     [8]  Restore from requirements.txt' -ForegroundColor DarkGray; Write-Host ''; Write-Host '     [9]  Run as Administrator' -ForegroundColor DarkYellow; Write-Host '     [0]  Exit' -ForegroundColor DarkRed; Write-Host ''; Write-Host '  ---------------------------------------------------------------------------' -ForegroundColor DarkGray; Write-Host '  Select option: ' -ForegroundColor DarkCyan -NoNewline"

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
"%interp%" -m pip install %module%
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
"%interp%" -m pip uninstall %module% -y
call :result
goto main


:list
cls
call :header "INSTALLED PACKAGES"
ECHO.
"%interp%" -m pip list --format=columns
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
    for /f "skip=2 tokens=1 delims= " %%p in ('"%interp%" -m pip list --outdated --format=columns 2^>nul') do (
        set "pkg_list=!pkg_list! %%p"
    )
    if defined pkg_list (
        powershell -NoProfile -Command "Write-Host '  [..] Upgrading:!pkg_list!' -ForegroundColor DarkGray"
        ECHO.
        "%interp%" -m pip install --upgrade !pkg_list!
        ECHO.
        powershell -NoProfile -Command "Write-Host '  [OK] All packages updated' -ForegroundColor DarkGreen"
    ) else (
        powershell -NoProfile -Command "Write-Host '  [OK] All packages are up to date' -ForegroundColor DarkGreen"
    )
) else (
    powershell -NoProfile -Command "Write-Host '  [..] Updating %module%...' -ForegroundColor DarkYellow"
    ECHO.
    "%interp%" -m pip install %module% --upgrade
)
call :result
goto main


:info
cls
call :header "PACKAGE INFO"
call :promptModule "Package name: "
if not defined module goto main
ECHO.
"%interp%" -m pip show %module%
call :result
goto main


:outdated
cls
call :header "OUTDATED PACKAGES"
ECHO.
powershell -NoProfile -Command "Write-Host '  [..] Checking for updates...' -ForegroundColor DarkYellow"
ECHO.
"%interp%" -m pip list --outdated --format=columns
call :result
goto main


:backup
cls
call :header "BACKUP PACKAGES"
set "backup_file=%backup_prefix%_requirements.txt"
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
"%interp%" -m pip freeze > "%backup_file%"
ECHO.
powershell -NoProfile -Command "Write-Host '  [OK] Saved: ' -ForegroundColor DarkGreen -NoNewline; Write-Host '%cd%\%backup_file%' -ForegroundColor Gray"
call :result
goto main


:restore
cls
call :header "RESTORE PACKAGES"
set "backup_file=%backup_prefix%_requirements.txt"
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
powershell -NoProfile -Command "Get-Content '%backup_file%' | ForEach-Object { Write-Host ('     ' + $_) -ForegroundColor DarkGray }"
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
    "%interp%" -m pip install -r "%backup_file%"
    ECHO.
    powershell -NoProfile -Command "Write-Host '  [OK] Restore complete' -ForegroundColor DarkGreen"
)
call :result
goto main


:admin
ECHO.
powershell -NoProfile -Command "Write-Host '  [..] Requesting administrator privileges...' -ForegroundColor DarkYellow"
:: Pass the (space-containing) interpreter/label via env vars so PowerShell reads them at runtime
:: ($env:) instead of them being expanded onto the cmd line — that would let cmd's quote toggling
:: split a path like "C:\Program Files\...". Start-Process bakes them into the elevated child's
:: quoted args. (Env vars are scoped by SETLOCAL, cleared at :end.)
set "PM_INTERP=%interp%"
set "PM_LABEL=%label%"
set "PM_PREFIX=%backup_prefix%"
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList ('\"' + $env:PM_INTERP + '\" \"' + $env:PM_LABEL + '\" \"' + $env:PM_PREFIX + '\"') -WorkingDirectory '%cd%' -Verb RunAs"
goto end


:promptModule
ECHO.
powershell -NoProfile -Command "Write-Host '  %~1' -ForegroundColor Gray -NoNewline"
set "module="
set /p "module="
goto :eof


:header
powershell -NoProfile -Command "Write-Host ''; Write-Host '  ===========================================================================' -ForegroundColor DarkGray; Write-Host ('   ' + '%~1') -ForegroundColor Gray; Write-Host '  ===========================================================================' -ForegroundColor DarkGray"
goto :eof


:result
ECHO.
powershell -NoProfile -Command "Write-Host '  ---------------------------------------------------------------------------' -ForegroundColor DarkGray; Write-Host '  Press any key to continue...' -ForegroundColor DarkGray"
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
