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
:: Optional env contract (exported by a wrapper BEFORE handoff):
::   PM_PIP_TARGET  Directory installs must land in, for hosts whose interpreter does
::                  not read the user site (Blender: pip's silent no-admin fallback goes
::                  to a user site the app never imports). Install/update/restore then
::                  run a resolver-aware two-step -- pip plans with --dry-run --report
::                  against the interpreter's own site-packages (so a dep the host
::                  already bundles is never re-downloaded to shadow it), and only the
::                  reported set is applied with --no-deps --target. Every other pip op
::                  sees the targeted dists via PYTHONPATH (uninstall included).
:: No elevation option. Nothing here needs one: without it pip lands in the user site
:: (which Maya reads) or, in targeted mode, in PM_PIP_TARGET. Someone who wants a
:: machine-wide install into the interpreter's own site-packages right-clicks the
:: launcher > Run as administrator; the title then says ADMINISTRATOR so the two
:: cannot be confused. The in-menu relaunch this replaces never worked: the batfile
:: `runas` verb is `cmd /C "%1" %*` (unlike `open`, `"%1" %*`), and with more than
:: two quote characters on the line cmd strips the first and the last -- so the
:: elevated window ran a mangled path and closed before drawing anything, while the
:: window that launched it said Goodbye (measured 2026-08-25, with and without spaces
:: in the path).
:: ASCII-only output (no box-drawing chars) so it is robust to the cmd UTF-8 codepage parsing bug.
:: A literal `!` must be written `^!` -- delayed expansion silently swallows a bare one
:: (that is why the status markers below are `[^!^!]`, not `[!!]`).

set "ver=1.4.0"
set "PYTHONIOENCODING=utf-8"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"

set "interp=%~1"
set "label=%~2"
set "backup_prefix=%~3"
if not defined label set "label=Python"
if not defined backup_prefix set "backup_prefix=python"

:: Anything PowerShell needs that can hold a path travels in the environment and is read back
:: with $env:. Inlining it into a single-quoted literal breaks on the first apostrophe -- a
:: profile like C:\Users\O'Brien closes the string and the whole line dies on a parser error --
:: and inlining it onto the command line lets cmd's quote toggling split "C:\Program Files\...".
set "PM_INTERP=%interp%"
set "PM_LABEL=%label%"
set "PM_PREFIX=%backup_prefix%"
set "PM_CWD=%cd%"

:: --- Targeted mode (see the PM_PIP_TARGET contract above) -------------------
:: -s keeps every pip op blind to the user site, so what pip sees matches what the
:: host can import; PYTHONPATH makes prior targeted installs count as installed.
set "iflags="
if defined PM_PIP_TARGET (
    set "iflags=-s"
    set "PYTHONPATH=%PM_PIP_TARGET%"
)

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

:: --- Elevation state -------------------------------------------------------
:: fltmc needs an administrator token and fails without one; no prompt either way.
:: The title carries the verdict (see the header for why there is no menu item).
set "mode="
fltmc >nul 2>&1 && set "mode= (ADMINISTRATOR)"

:validateInterp
IF NOT EXIST "%interp%" (
    powershell -NoProfile -Command "Write-Host ('%C_ERR%  [^!^!] Interpreter not found: %C_MUTED%' + $env:PM_INTERP + '%C_RESET%')"
    powershell -NoProfile -Command "Write-Host '%C_FAINT%  Usage: package-manager.bat <python.exe> <Label> <backup_prefix>%C_RESET%'"
    ECHO.
    powershell -NoProfile -Command "Write-Host '%C_FAINT%  Press any key to close...%C_RESET%'"
    pause >nul
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
powershell -NoProfile -Command "$w=76; $blank=' '*$w; $t=('%label% PACKAGE MANAGER%mode%').ToUpper(); $v='v%ver%'; $tL=$t.PadLeft([int](($w-$t.Length)/2)+$t.Length).PadRight($w); $vL=$v.PadLeft([int](($w-$v.Length)/2)+$v.Length).PadRight($w); Write-Host ('%C_BANNER%'+$blank+'%C_RESET%'); Write-Host ('%C_BANNER%'+$tL+'%C_RESET%'); Write-Host ('%C_BANNER_DIM%'+$vL+'%C_RESET%'); Write-Host ('%C_BANNER%'+$blank+'%C_RESET%')"
ECHO.
powershell -NoProfile -Command "Write-Host '%C_OK%  [OK] %C_MUTED%%label% Python interpreter ready%C_RESET%'"
timeout /t 1 >nul
goto main


:main
cls
ECHO.
powershell -NoProfile -Command "Write-Host '%C_RULE%  ===========================================================================%C_RESET%'; Write-Host '%C_TITLE%   %label% PACKAGE MANAGER%mode%%C_RESET%'; Write-Host '%C_RULE%  ===========================================================================%C_RESET%'; Write-Host ''; Write-Host '%C_KEY%     [1]%C_TEXT%  Install Package(s)%C_RESET%'; Write-Host '%C_KEY%     [2]%C_TEXT%  Update Package(s)%C_RESET%'; Write-Host '%C_KEY%     [3]%C_TEXT%  Uninstall Package(s)%C_RESET%'; Write-Host '%C_KEY%     [4]%C_TEXT%  Show Package Info%C_RESET%'; Write-Host '%C_KEY%     [5]%C_TEXT%  List Installed Packages%C_RESET%'; Write-Host '%C_KEY%     [6]%C_TEXT%  Check Outdated Packages%C_RESET%'; Write-Host ''; Write-Host '%C_KEY2%     [7]%C_MUTED%  Backup to requirements.txt%C_RESET%'; Write-Host '%C_KEY2%     [8]%C_MUTED%  Restore from requirements.txt%C_RESET%'; Write-Host ''; Write-Host '%C_ERR%     [0]  Exit%C_RESET%'; Write-Host ''; Write-Host '%C_RULE%  ---------------------------------------------------------------------------%C_RESET%'; Write-Host '%C_PROMPT%  Select option: %C_RESET%' -NoNewline"

CHOICE /C:123456780 /N

IF ERRORLEVEL 9 goto end
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
call :header "INSTALL PACKAGE(S)"
call :promptModule "Package name(s), comma separated (e.g. scipy, numpy==2.1): "
if not defined module goto main
ECHO.
powershell -NoProfile -Command "Write-Host ('%C_WORK%  [..] Installing ' + $env:PM_MODULE + '...%C_RESET%')"
ECHO.
if defined PM_PIP_TARGET (
    set "pt_flags="
    set "pt_args=!module!"
    call :pipTargeted
) else (
    "%interp%" -m pip install !module!
    set "rc=!ERRORLEVEL!"
    call :shadowDoctor
)
call :result net %rc%
goto main


:uninstall
cls
call :header "UNINSTALL PACKAGE(S)"
call :promptModule "Package name(s) to remove, comma separated: "
if not defined module goto main
ECHO.
powershell -NoProfile -Command "Write-Host ('%C_WORK%  [..] Removing ' + $env:PM_MODULE + '...%C_RESET%')"
ECHO.
"%interp%" %iflags% -m pip uninstall !module! -y
call :result
goto main


:list
cls
call :header "INSTALLED PACKAGES"
ECHO.
"%interp%" %iflags% -m pip list --format=columns
call :result
goto main


:update
cls
call :header "UPDATE PACKAGE(S)"
call :promptModule "Package name(s), comma separated (or ALL for everything): "
if not defined module goto main
ECHO.
if /I "!module!"=="all" (
    powershell -NoProfile -Command "Write-Host '%C_WORK%  [..] Checking for outdated packages...%C_RESET%'"
    set "pkg_list="
    for /f "skip=2 tokens=1 delims= " %%p in ('"%interp%" %iflags% -m pip list --outdated --format=columns 2^>nul') do (
        set "pkg_list=!pkg_list! %%p"
    )
    if defined pkg_list (
        powershell -NoProfile -Command "Write-Host '%C_FAINT%  [..] Upgrading:!pkg_list!%C_RESET%'"
        ECHO.
        if defined PM_PIP_TARGET (
            set "pt_flags=--upgrade"
            set "pt_args=!pkg_list!"
            call :pipTargeted
        ) else (
            "%interp%" -m pip install --upgrade !pkg_list!
            set "rc=!ERRORLEVEL!"
            call :shadowDoctor
        )
        ECHO.
        if "!rc!"=="0" (
            powershell -NoProfile -Command "Write-Host '%C_OK%  [OK] All packages updated%C_RESET%'"
        ) else (
            powershell -NoProfile -Command "Write-Host '%C_ERR%  [^!^!] One or more packages failed to update%C_RESET%'"
        )
    ) else (
        set "rc=0"
        powershell -NoProfile -Command "Write-Host '%C_OK%  [OK] All packages are up to date%C_RESET%'"
    )
) else (
    powershell -NoProfile -Command "Write-Host ('%C_WORK%  [..] Updating ' + $env:PM_MODULE + '...%C_RESET%')"
    ECHO.
    if defined PM_PIP_TARGET (
        set "pt_flags=--upgrade"
        set "pt_args=!module!"
        call :pipTargeted
    ) else (
        "%interp%" -m pip install !module! --upgrade
        set "rc=!ERRORLEVEL!"
        call :shadowDoctor
    )
)
call :result net %rc%
goto main


:info
cls
call :header "PACKAGE INFO"
call :promptModule "Package name(s), comma separated: "
if not defined module goto main
ECHO.
"%interp%" %iflags% -m pip show !module!
call :result
goto main


:outdated
cls
call :header "OUTDATED PACKAGES"
ECHO.
powershell -NoProfile -Command "Write-Host '%C_WORK%  [..] Checking for updates...%C_RESET%'"
ECHO.
"%interp%" %iflags% -m pip list --outdated --format=columns
call :result net
goto main


:backup
cls
call :header "BACKUP PACKAGES"
set "backup_file=%backup_prefix%_requirements.txt"
set "PM_BACKUP=%backup_file%"
ECHO.
IF EXIST "%backup_file%" (
    powershell -NoProfile -Command "Write-Host ('%C_WARN%  [^!^!] ' + $env:PM_BACKUP + ' already exists. Overwrite? [Y/N]: %C_RESET%') -NoNewline"
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
"%interp%" %iflags% -m pip freeze > "%backup_file%"
ECHO.
powershell -NoProfile -Command "Write-Host ('%C_OK%  [OK] Saved: %C_MUTED%' + $env:PM_CWD + '\' + $env:PM_BACKUP + '%C_RESET%')"
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
set "PM_BACKUP=%backup_file%"
ECHO.
powershell -NoProfile -Command "Write-Host ('%C_TITLE%  Packages in ' + $env:PM_BACKUP + ':%C_RESET%')"
ECHO.
powershell -NoProfile -Command "Get-Content -LiteralPath $env:PM_BACKUP | ForEach-Object { Write-Host ('%C_FAINT%     ' + $_ + '%C_RESET%') }"
ECHO.
powershell -NoProfile -Command "Write-Host '%C_WARN%  Proceed with restore? [Y/N]: %C_RESET%' -NoNewline"
CHOICE /C:YN /N
:: Default to N so Ctrl+C / errorlevel 0 cancels rather than restoring.
set "ans=N"
IF ERRORLEVEL 1 set "ans=Y"
IF ERRORLEVEL 2 set "ans=N"
IF /I "%ans%"=="N" (
    set "rc=0"
    ECHO.
    powershell -NoProfile -Command "Write-Host '%C_FAINT%  [--] Cancelled%C_RESET%'"
) ELSE (
    ECHO.
    powershell -NoProfile -Command "Write-Host '%C_WORK%  [..] Restoring packages...%C_RESET%'"
    ECHO.
    if defined PM_PIP_TARGET (
        set "pt_flags=--upgrade"
        set "pt_args=-r "%backup_file%""
        call :pipTargeted
    ) else (
        "%interp%" -m pip install -r "%backup_file%"
        set "rc=!ERRORLEVEL!"
        call :shadowDoctor
    )
    ECHO.
    if "!rc!"=="0" (
        powershell -NoProfile -Command "Write-Host '%C_OK%  [OK] Restore complete%C_RESET%'"
    ) else (
        powershell -NoProfile -Command "Write-Host '%C_ERR%  [^!^!] Restore failed - see the output above%C_RESET%'"
    )
)
call :result net %rc%
goto main


:promptModule
ECHO.
powershell -NoProfile -Command "Write-Host '%C_PROMPT%  %~1%C_RESET%' -NoNewline"
set "module="
set /p "module="
if not defined module goto :eof
:: A list only needs its commas turned into spaces -- pip takes several requirements in one
:: call -- so doing it in the prompt every operation shares gives install / update / uninstall
:: / show the feature at once, with no operation changed. But a comma is NOT always a
:: separator: it is part of the requirement inside a version range (django>=2.0,<3.0) and an
:: extras list (requests[security,socks]). "comma space" is unambiguous -- no pip requirement
:: contains one -- so that always splits. A bare comma splits only when nothing in the value
:: looks like a range or an extras list, which keeps `scipy,numpy` working without ever
:: breaking a valid requirement apart.
set "module=!module:, = !"
:: Does what is left look like a single requirement rather than a list? `=` cannot be searched
:: for with the replace syntax -- it is that syntax's own delimiter, so `!module:==!` reads as an
:: EMPTY search and matches everything -- hence the for/f split on it. And none of this may go
:: through a pipe: the shell that runs a pipe re-parses the expanded text, so a `<` or `>` from
:: a version range would turn into a redirection.
set "spec="
for /f "tokens=1,* delims==" %%A in ("!module!") do if not "%%B"=="" set "spec=1"
if not "!module!"=="!module:<=!" set "spec=1"
if not "!module!"=="!module:>=!" set "spec=1"
if not "!module!"=="!module:[=!" set "spec=1"
if not defined spec set "module=!module:,= !"
:: The typed value is echoed back in the messages below, and it is the one thing here a user
:: can put anything into -- it reaches PowerShell through the environment for the same reason
:: paths do (see the header).
set "PM_MODULE=!module!"
goto :eof


:pipTargeted
:: Resolver-aware install into %PM_PIP_TARGET%. Consumes pt_args (requirements or
:: -r <file>) + pt_flags (--upgrade or empty); leaves the verdict in rc.
:: Two steps because a raw "pip install --target" plans a COMPLETE standalone
:: closure, ignoring what the interpreter ships -- measured planting numpy 2.5.2
:: over Blender's bundled 2.3.4. Step 1 lets pip's own resolver plan against the
:: bundled site-packages; step 2 applies exactly the reported pins, no resolution.
set "PM_REPORT=%TEMP%\pm_report_%RANDOM%.json"
set "PM_PLAN=%TEMP%\pm_plan_%RANDOM%.txt"
"%interp%" -s -m pip install --dry-run --report "%PM_REPORT%" !pt_flags! !pt_args!
set "rc=!ERRORLEVEL!"
if not "!rc!"=="0" goto pipTargetedDone
:: No report despite a zero exit = pip did not produce a plan (too old for
:: --report, or it wrote elsewhere). Reporting "already satisfied" there would
:: claim an install that never happened, so fail loudly instead.
if not exist "%PM_REPORT%" (
    powershell -NoProfile -Command "Write-Host '%C_ERR%  [^!^!] pip produced no install report - cannot plan the install.%C_RESET%'; Write-Host '%C_FAINT%  --dry-run --report needs pip 22.2 or newer; upgrade pip for this interpreter.%C_RESET%'"
    set "rc=1"
    goto pipTargetedDone
)
:: Where-Object guards a report whose rows carry no metadata.name -- @($r.install)
:: on a MISSING install key yields one $null element, which would otherwise emit a
:: bare "==" and be handed to pip as a requirement.
powershell -NoProfile -Command "$r = Get-Content -LiteralPath $env:PM_REPORT -Raw | ConvertFrom-Json; @($r.install) | Where-Object { $_.metadata.name } | ForEach-Object { $_.metadata.name + '==' + $_.metadata.version } | Set-Content -LiteralPath $env:PM_PLAN -Encoding ascii"
set "pins="
if exist "%PM_PLAN%" for /f "usebackq delims=" %%L in ("%PM_PLAN%") do set "pins=!pins! %%L"
if not defined pins (
    powershell -NoProfile -Command "Write-Host '%C_OK%  [OK] Already satisfied - nothing to install%C_RESET%'"
    set "rc=0"
    goto pipTargetedDone
)
ECHO.
"%interp%" -s -m pip install --no-deps --upgrade --target "%PM_PIP_TARGET%" !pins!
set "rc=!ERRORLEVEL!"
goto pipTargetedDone

:pipTargetedDone
del /f /q "%PM_REPORT%" "%PM_PLAN%" >nul 2>&1
goto :eof


:shadowDoctor
:: Post-install advisory for hosts that DO read the user site (Maya): warn when a
:: user-site dist shadows a different version the interpreter bundles (shiboken6 /
:: PySide6 / numpy -- the class of breakage pip performs silently). Read-only,
:: never blocks. Targeted mode is structurally shadow-proof, so it is skipped.
if defined PM_PIP_TARGET goto :eof
if exist "%~dp0pm_doctor.py" "%interp%" "%~dp0pm_doctor.py"
goto :eof


:header
powershell -NoProfile -Command "Write-Host ''; Write-Host '%C_RULE%  ===========================================================================%C_RESET%'; Write-Host '%C_TITLE%   %~1%C_RESET%'; Write-Host '%C_RULE%  ===========================================================================%C_RESET%'"
goto :eof


:result
:: %1  `net` from an operation that reaches PyPI. pip reports what failed but never that the
::     outgoing connection itself is blocked -- invisible from inside pip, and the failure
::     users actually hit behind a firewall. Local-only ops stay quiet: a missing package is
::     not a network problem, and guessing at one there is noise.
:: %2  the op's exit code, where the caller captured it because its own verdict line runs
::     after pip. Otherwise it is read here, before the ECHO below would clobber it.
set "op_rc=%ERRORLEVEL%"
if not "%~2"=="" set "op_rc=%~2"
ECHO.
if "%~1"=="net" if not "%op_rc%"=="0" (
    powershell -NoProfile -Command "Write-Host '%C_WARN%  [^!^!] That command failed (exit code %op_rc%).%C_RESET%'; Write-Host '%C_FAINT%  If it could not reach the network, check that a firewall or antivirus is not%C_RESET%'; Write-Host '%C_FAINT%  blocking outgoing connections for this Python, and that any proxy is configured.%C_RESET%'"
    ECHO.
)
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
