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
:: A literal `!` must be written `^!` -- delayed expansion silently swallows a bare one
:: (that is why the status markers below are `[^!^!]`, not `[!!]`).

set "ver=1.0.0"
set "PYTHONIOENCODING=utf-8"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"

set "interp=%~1"
set "label=%~2"
set "backup_prefix=%~3"
if not defined label set "label=Python"
if not defined backup_prefix set "backup_prefix=python"

:: --- Palette ----------------------------------------------------------------
:: 24-bit ANSI SGR (pastel on dark) rather than the 16 legacy console names, which
:: have no pastel range. cmd.exe turns on VT processing for its console on Win10+
:: and the powershell children write to that same screen buffer, so the sequences
:: render in conhost and Windows Terminal alike. ESC is captured via the `prompt $E`
:: trick; if that fails -- or NO_COLOR is set -- every C_* entry is blanked so the
:: menu degrades to plain text instead of spraying raw escape codes.
::   TITLE mauve . TEXT/MUTED/FAINT greys . RULE separators . KEY/KEY2 menu keys
::   OK green . WORK yellow . WARN peach . ERR red . PROMPT lavender
set "ESC="
for /F "delims=#" %%E in ('"prompt #$E# & for %%E in (1) do rem"') do set "ESC=%%E"
if defined NO_COLOR set "ESC="
set "C_RESET=%ESC%[0m"
set "C_TITLE=%ESC%[38;2;203;166;247m"
set "C_TEXT=%ESC%[38;2;205;214;244m"
set "C_MUTED=%ESC%[38;2;166;173;200m"
set "C_FAINT=%ESC%[38;2;127;132;156m"
set "C_RULE=%ESC%[38;2;88;91;112m"
set "C_KEY=%ESC%[38;2;137;180;250m"
set "C_KEY2=%ESC%[38;2;137;220;235m"
set "C_OK=%ESC%[38;2;166;227;161m"
set "C_WORK=%ESC%[38;2;249;226;175m"
set "C_WARN=%ESC%[38;2;250;179;135m"
set "C_ERR=%ESC%[38;2;243;139;168m"
set "C_PROMPT=%ESC%[38;2;180;190;254m"
set "C_BANNER=%ESC%[48;2;203;166;247m%ESC%[38;2;30;30;46m"
set "C_BANNER_DIM=%ESC%[48;2;203;166;247m%ESC%[38;2;69;71;90m"
if not defined ESC (for /F "tokens=1 delims==" %%V in ('set C_ 2^>nul') do set "%%V=")

:validateInterp
IF NOT EXIST "%interp%" (
    powershell -NoProfile -Command "Write-Host '%C_ERR%  [^!^!] Interpreter not found: %C_MUTED%%interp%%C_RESET%'"
    powershell -NoProfile -Command "Write-Host '%C_FAINT%  Usage: package-manager.bat <python.exe> <Label> <backup_prefix>%C_RESET%'"
    timeout /t 3 >nul
    ENDLOCAL
    exit /b 1
)
"%interp%" -m pip --version >nul 2>&1
IF ERRORLEVEL 1 (
    powershell -NoProfile -Command "Write-Host '%C_WORK%  [..] Installing pip...%C_RESET%'"
    "%interp%" -m ensurepip --upgrade >nul 2>&1
)

:intro
cls
color 07
ECHO.
powershell -NoProfile -Command "$w=76; $blank=' '*$w; $t=('%label% PACKAGE MANAGER').ToUpper(); $v='v%ver%'; $tL=$t.PadLeft([int](($w-$t.Length)/2)+$t.Length).PadRight($w); $vL=$v.PadLeft([int](($w-$v.Length)/2)+$v.Length).PadRight($w); Write-Host ('%C_BANNER%'+$blank+'%C_RESET%'); Write-Host ('%C_BANNER%'+$tL+'%C_RESET%'); Write-Host ('%C_BANNER_DIM%'+$vL+'%C_RESET%'); Write-Host ('%C_BANNER%'+$blank+'%C_RESET%')"
ECHO.
powershell -NoProfile -Command "Write-Host '%C_OK%  [OK] %C_MUTED%%label% Python interpreter ready%C_RESET%'"
timeout /t 1 >nul
goto main


:main
cls
ECHO.
powershell -NoProfile -Command "Write-Host '%C_RULE%  ===========================================================================%C_RESET%'; Write-Host '%C_TITLE%   %label% PACKAGE MANAGER%C_RESET%'; Write-Host '%C_RULE%  ===========================================================================%C_RESET%'; Write-Host ''; Write-Host '%C_KEY%     [1]%C_TEXT%  Install Package%C_RESET%'; Write-Host '%C_KEY%     [2]%C_TEXT%  Update Package%C_RESET%'; Write-Host '%C_KEY%     [3]%C_TEXT%  Uninstall Package%C_RESET%'; Write-Host '%C_KEY%     [4]%C_TEXT%  Show Package Info%C_RESET%'; Write-Host '%C_KEY%     [5]%C_TEXT%  List Installed Packages%C_RESET%'; Write-Host '%C_KEY%     [6]%C_TEXT%  Check Outdated Packages%C_RESET%'; Write-Host ''; Write-Host '%C_KEY2%     [7]%C_MUTED%  Backup to requirements.txt%C_RESET%'; Write-Host '%C_KEY2%     [8]%C_MUTED%  Restore from requirements.txt%C_RESET%'; Write-Host ''; Write-Host '%C_WARN%     [9]  Run as Administrator%C_RESET%'; Write-Host '%C_ERR%     [0]  Exit%C_RESET%'; Write-Host ''; Write-Host '%C_RULE%  ---------------------------------------------------------------------------%C_RESET%'; Write-Host '%C_PROMPT%  Select option: %C_RESET%' -NoNewline"

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
powershell -NoProfile -Command "Write-Host '%C_WORK%  [..] Installing %module%...%C_RESET%'"
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
powershell -NoProfile -Command "Write-Host '%C_WORK%  [..] Removing %module%...%C_RESET%'"
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
    powershell -NoProfile -Command "Write-Host '%C_WORK%  [..] Checking for outdated packages...%C_RESET%'"
    set "pkg_list="
    for /f "skip=2 tokens=1 delims= " %%p in ('"%interp%" -m pip list --outdated --format=columns 2^>nul') do (
        set "pkg_list=!pkg_list! %%p"
    )
    if defined pkg_list (
        powershell -NoProfile -Command "Write-Host '%C_FAINT%  [..] Upgrading:!pkg_list!%C_RESET%'"
        ECHO.
        "%interp%" -m pip install --upgrade !pkg_list!
        ECHO.
        powershell -NoProfile -Command "Write-Host '%C_OK%  [OK] All packages updated%C_RESET%'"
    ) else (
        powershell -NoProfile -Command "Write-Host '%C_OK%  [OK] All packages are up to date%C_RESET%'"
    )
) else (
    powershell -NoProfile -Command "Write-Host '%C_WORK%  [..] Updating %module%...%C_RESET%'"
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
powershell -NoProfile -Command "Write-Host '%C_WORK%  [..] Checking for updates...%C_RESET%'"
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
    powershell -NoProfile -Command "Write-Host '%C_WARN%  [^!^!] %backup_file% already exists. Overwrite? [Y/N]: %C_RESET%' -NoNewline"
    CHOICE /C:YN /N
    :: Default to N so Ctrl+C / errorlevel 0 cancels rather than overwrites.
    set "ans=N"
    IF ERRORLEVEL 1 set "ans=Y"
    IF ERRORLEVEL 2 set "ans=N"
    IF /I "!ans!"=="N" (
        ECHO.
        powershell -NoProfile -Command "Write-Host '%C_FAINT%  [--] Cancelled%C_RESET%'"
        call :result
        goto main
    )
    ECHO.
)
powershell -NoProfile -Command "Write-Host '%C_WORK%  [..] Creating backup...%C_RESET%'"
"%interp%" -m pip freeze > "%backup_file%"
ECHO.
powershell -NoProfile -Command "Write-Host '%C_OK%  [OK] Saved: %C_MUTED%%cd%\%backup_file%%C_RESET%'"
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
        powershell -NoProfile -Command "Write-Host '%C_ERR%  [^!^!] No requirements file found%C_RESET%'"
        call :result
        goto main
    )
)
ECHO.
powershell -NoProfile -Command "Write-Host '%C_TITLE%  Packages in %backup_file%:%C_RESET%'"
ECHO.
powershell -NoProfile -Command "Get-Content '%backup_file%' | ForEach-Object { Write-Host ('%C_FAINT%     ' + $_ + '%C_RESET%') }"
ECHO.
powershell -NoProfile -Command "Write-Host '%C_WARN%  Proceed with restore? [Y/N]: %C_RESET%' -NoNewline"
CHOICE /C:YN /N
:: Default to N so Ctrl+C / errorlevel 0 cancels rather than restoring.
set "ans=N"
IF ERRORLEVEL 1 set "ans=Y"
IF ERRORLEVEL 2 set "ans=N"
IF /I "%ans%"=="N" (
    ECHO.
    powershell -NoProfile -Command "Write-Host '%C_FAINT%  [--] Cancelled%C_RESET%'"
) ELSE (
    ECHO.
    powershell -NoProfile -Command "Write-Host '%C_WORK%  [..] Restoring packages...%C_RESET%'"
    ECHO.
    "%interp%" -m pip install -r "%backup_file%"
    ECHO.
    powershell -NoProfile -Command "Write-Host '%C_OK%  [OK] Restore complete%C_RESET%'"
)
call :result
goto main


:admin
ECHO.
powershell -NoProfile -Command "Write-Host '%C_WORK%  [..] Requesting administrator privileges...%C_RESET%'"
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
powershell -NoProfile -Command "Write-Host '%C_PROMPT%  %~1%C_RESET%' -NoNewline"
set "module="
set /p "module="
goto :eof


:header
powershell -NoProfile -Command "Write-Host ''; Write-Host '%C_RULE%  ===========================================================================%C_RESET%'; Write-Host '%C_TITLE%   %~1%C_RESET%'; Write-Host '%C_RULE%  ===========================================================================%C_RESET%'"
goto :eof


:result
ECHO.
powershell -NoProfile -Command "Write-Host '%C_RULE%  ---------------------------------------------------------------------------%C_RESET%'; Write-Host '%C_FAINT%  Press any key to continue...%C_RESET%'"
pause >nul
goto :eof


:end
cls
ECHO.
powershell -NoProfile -Command "Write-Host '%C_PROMPT%  Goodbye^!%C_RESET%'"
ECHO.
timeout /t 1 >nul
ENDLOCAL
exit /b 0
