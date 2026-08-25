@ECHO off
SETLOCAL EnableDelayedExpansion EnableExtensions
:: Maya Python Package Manager (thin wrapper) for Windows.
:: Detects Maya, resolves mayapy.exe, then hands off to the shared, interpreter-agnostic
:: package-manager.bat (m3trik\package-manager.bat) which owns the menu/operations.
:: Usage: mayapy-package-manager.bat [maya_version]
::   maya_version is optional; if omitted, auto-detects installs under
::   %ProgramFiles%\Autodesk\Maya* and prompts only when there is an actual choice to make.
:: A literal "!" must be written "^!" -- delayed expansion silently swallows a bare one
:: (that is why the status markers below are [^!^!], not [!!]).
set "preselected_version=%~1"

:setVersion
:: Auto-detect Maya versions by scanning the default Autodesk install dir (the "scan strategy").
set "found_versions="
set "latest_version="
set "version_count=0"
for /f "tokens=*" %%D in ('dir /b /ad "%ProgramFiles%\Autodesk\Maya*" 2^>nul') do (
    set "ver_str=%%D"
    set "ver_num=!ver_str:Maya=!"
    set "non_digit="
    for /f "delims=0123456789" %%X in ("!ver_num!") do set "non_digit=%%X"
    if not defined non_digit if exist "%ProgramFiles%\Autodesk\Maya!ver_num!\bin\mayapy.exe" (
        set "found_versions=!found_versions! !ver_num!"
        set "latest_version=!ver_num!"
        set /a version_count+=1
    )
)

if defined found_versions (
    powershell -NoProfile -Command "Write-Host '  [OK] Detected Maya installations:' -ForegroundColor DarkGreen -NoNewline; Write-Host '%found_versions%' -ForegroundColor DarkYellow"
) else (
    powershell -NoProfile -Command "Write-Host '  [^!^!] No Maya installations detected in default location' -ForegroundColor DarkRed"
)

ECHO.
if defined preselected_version (
    set "maya_version=%preselected_version%"
    set "preselected_version="
) else if "%version_count%"=="1" (
    REM Exactly one install -- there is nothing to pick, so don't make the user confirm it.
    set "maya_version=%latest_version%"
    powershell -NoProfile -Command "Write-Host '  [OK] Using Maya %latest_version% (only install found)' -ForegroundColor DarkGreen"
) else if defined latest_version (
    powershell -NoProfile -Command "Write-Host '  Enter Maya version [%latest_version%]: ' -ForegroundColor Gray -NoNewline"
    set "maya_version="
    set /p "maya_version="
    if not defined maya_version set "maya_version=%latest_version%"
) else (
    powershell -NoProfile -Command "Write-Host '  Enter Maya version: ' -ForegroundColor Gray -NoNewline"
    set /p "maya_version="
)
if not defined maya_version goto noVersion
set "mayapy=%ProgramFiles%\Autodesk\Maya%maya_version%\bin\mayapy.exe"

:validateMayapyPath
IF EXIST "%mayapy%" goto handoff
powershell -NoProfile -Command "Write-Host '  [^!^!] Maya %maya_version% not found' -ForegroundColor DarkRed"
ECHO.
powershell -NoProfile -Command "Write-Host '  Enter full path to mayapy.exe (blank to retry version): ' -ForegroundColor Gray -NoNewline"
set "mayapy="
set /p "mayapy="
if not defined mayapy goto setVersion
goto validateMayapyPath

:handoff
:: Locate the shared menu: alongside this wrapper (distributed), in the monorepo
:: (m3trik), or -- when this wrapper was downloaded on its own -- lifted out of the
:: published wheel by pip, so one download is enough to bootstrap.
set "generic=%~dp0package-manager.bat"
call :checkGeneric
if defined generic goto runGeneric
set "generic=%~dp0..\..\..\m3trik\package-manager.bat"
call :checkGeneric
if defined generic goto runGeneric
call :fetchShared
if defined generic goto runGeneric
goto fetchFailed

:runGeneric
call "%generic%" "%mayapy%" "Maya %maya_version%" "maya%maya_version%"
ENDLOCAL
exit /b %ERRORLEVEL%

:checkGeneric
:: Keep %generic% only if it really is the shared menu. A missing, empty, truncated or wrong
:: file -- a stub left by an earlier failed bootstrap, or an HTML page saved by hand instead of
:: the raw one -- would be called, return instantly, and close the window with nothing on
:: screen: the exact silent failure this path exists to explain. :main is the menu's signature.
findstr /b /c:":main" "%generic%" >nul 2>&1
if errorlevel 1 set "generic="
goto :eof

:fetchShared
:: One-file bootstrap: lift the shared menu out of the published wheel, using the SAME
:: interpreter the menu is about to drive. pip rather than a raw download because pip honours
:: whatever proxy, index-url and CA bundle this machine is configured with -- the environments
:: that block a direct fetch are exactly the ones that have those set -- and because an
:: interpreter that cannot reach an index makes every menu operation useless anyway: better to
:: fail here once, with pip's own message on screen, than at the first install.
:: `download`, never `install`: the menu is 12 KB, and installing would write into Program Files
:: and demand elevation for what is only a bootstrap.
set "fetch_target=%~dp0package-manager.bat"
if not defined TEMP set "TEMP=%~dp0"
set "fetch_dir=%TEMP%\mayatk-pm-bootstrap"
ECHO.
powershell -NoProfile -Command "Write-Host '  [..] Fetching the shared menu from the mayatk wheel' -ForegroundColor Gray"
if exist "%fetch_dir%" rd /s /q "%fetch_dir%" >nul 2>&1
md "%fetch_dir%" >nul 2>&1
call :fetchWheel
REM 2 MB of wheel for one 12 KB file: never leave the scratch copy behind.
if exist "%fetch_dir%" rd /s /q "%fetch_dir%" >nul 2>&1
REM Nothing produced: %fetch_stage% still names the step that actually failed, which is what
REM :fetchFailed reports. Only claim to have verified a menu once there is one to verify.
if not exist "%fetch_target%" goto :eof
set "fetch_stage=verifying the downloaded menu"
set "generic=%fetch_target%"
call :checkGeneric
if defined generic goto :eof
del /f /q "%fetch_target%" >nul 2>&1
goto :eof

:fetchWheel
REM The same guarded ensurepip the menu itself runs -- a bare interpreter has no pip to fetch with.
"%mayapy%" -m pip --version >nul 2>&1
if errorlevel 1 "%mayapy%" -m ensurepip --upgrade >nul 2>&1
REM The menu sets this too; the bootstrap runs before the handoff, so set it here as well
REM or pip's "a new release is available" notice lands in the middle of the bootstrap.
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
REM --retries/--timeout bound the wait: pip's defaults stack to well over a minute against a
REM firewall that drops rather than refuses. --only-binary fails loudly instead of fetching an
REM sdist, which carries no built package-manager.bat to lift.
set "fetch_stage=downloading the mayatk wheel"
"%mayapy%" -m pip download --no-deps --only-binary=:all: --retries 1 --timeout 15 --dest "%fetch_dir%" mayatk
if errorlevel 1 goto :eof
REM A wheel is a zip; take the one member by name rather than unpacking 2 MB of it. Every path
REM travels in the environment and is read back with $env: -- inlined into a single-quoted
REM PowerShell literal, a profile like C:\Users\O'Brien would close the string and kill the line.
set "fetch_stage=unpacking the wheel"
set "PM_WHEEL_DIR=%fetch_dir%"
set "PM_MENU_PATH=%fetch_target%"
set "PM_WHEEL_ENTRY=mayatk/env_utils/package-manager.bat"
powershell -NoProfile -Command "try { $w = Get-ChildItem -LiteralPath $env:PM_WHEEL_DIR -Filter '*.whl' | Select-Object -First 1; if (-not $w) { exit 2 }; Add-Type -AssemblyName System.IO.Compression.FileSystem; $z = [IO.Compression.ZipFile]::OpenRead($w.FullName); try { $e = $z.Entries | Where-Object { $_.FullName -eq $env:PM_WHEEL_ENTRY }; if (-not $e) { exit 3 }; [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $env:PM_MENU_PATH, $true) } finally { $z.Dispose() } } catch { exit 4 }"
if errorlevel 1 goto :eof
REM cmd.exe mis-scans LF-only bats - normalize whatever the wheel happened to carry.
powershell -NoProfile -Command "$p = $env:PM_MENU_PATH; $t = [IO.File]::ReadAllText($p); [IO.File]::WriteAllText($p, ($t -replace '\r', '' -replace '\n', ([char]13 + [char]10)))"
goto :eof

:fetchFailed
:: Dead end, and the window must NOT vanish on it -- explain, then wait for a keypress.
ECHO.
powershell -NoProfile -Command "Write-Host '  [^!^!] Could not obtain package-manager.bat' -ForegroundColor DarkRed"
ECHO.
set "PM_INTERP_PATH=%mayapy%"
set "PM_HERE=%~dp0"
powershell -NoProfile -Command "Write-Host '  This launcher is only a wrapper - the menu itself lives in a companion file' -ForegroundColor Gray; Write-Host '  named package-manager.bat. It is not in this folder, so the wrapper asked this' -ForegroundColor Gray; Write-Host '  interpreter to fetch the mayatk wheel and lift the file out of it:' -ForegroundColor Gray; Write-Host ('      ' + $env:PM_INTERP_PATH) -ForegroundColor DarkGray; Write-Host '  That failed while %fetch_stage% (its own message is above).' -ForegroundColor Gray"
ECHO.
powershell -NoProfile -Command "Write-Host '  The usual cause is that the interpreter cannot reach a package index:' -ForegroundColor DarkYellow; Write-Host '    - a firewall or antivirus is blocking it from reaching the internet. The menu' -ForegroundColor Gray; Write-Host '      needs that same access for every install, so this would fail there anyway.' -ForegroundColor Gray; Write-Host '    - a proxy or a private index is configured for pip but is not reachable' -ForegroundColor Gray; Write-Host '    - this interpreter has no working pip' -ForegroundColor Gray"
ECHO.
powershell -NoProfile -Command "Write-Host '  Any one of these fixes it:' -ForegroundColor DarkYellow; Write-Host '    1. Allow that interpreter outbound in the firewall, then rerun.' -ForegroundColor Gray; Write-Host '    2. pip install mayatk from any Python that can reach your index - the wheel puts' -ForegroundColor Gray; Write-Host '       package-manager.bat next to the wrapper for you.' -ForegroundColor Gray; Write-Host ('    3. Save this file by hand into ' + $env:PM_HERE + ' as package-manager.bat:') -ForegroundColor Gray; Write-Host '       https://raw.githubusercontent.com/m3trik/mayatk/master/mayatk/env_utils/package-manager.bat' -ForegroundColor DarkGray"
ECHO.
powershell -NoProfile -Command "Write-Host '  Press any key to close...' -ForegroundColor DarkGray"
pause >nul
ENDLOCAL
exit /b 1

:noVersion
:: Nothing detected and nothing entered. Without this the two prompts bounce forever when
:: stdin is not a console: `set /p` returns immediately on EOF and leaves the value unset.
ECHO.
powershell -NoProfile -Command "Write-Host '  [^!^!] No Maya version entered - nothing to do.' -ForegroundColor DarkRed"
ECHO.
powershell -NoProfile -Command "Write-Host '  Install Maya, or rerun with the version as an argument:' -ForegroundColor Gray; Write-Host '      mayapy-package-manager.bat 2025' -ForegroundColor DarkGray"
ECHO.
powershell -NoProfile -Command "Write-Host '  Press any key to close...' -ForegroundColor DarkGray"
pause >nul
ENDLOCAL
exit /b 1
