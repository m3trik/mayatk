@ECHO off
:: Maya Python Package Manager for Windows
:: Requires: pip needs to already have been installed to mayapy.

:intro
color 07
set ver=1.0.0
powershell -Command "Write-Host 'Maya Python Package Manager v%ver%' -ForegroundColor Cyan"
ECHO\

:setVersion
ECHO What is your Maya version? (ex. 2022)
set /p maya_version=
set mayapy="%ProgramFiles%\Autodesk\Maya%maya_version%\bin\mayapy.exe"
goto validateMayapyPath

:validateMayapyPath
IF EXIST %mayapy% (
    echo  Found Python interpreter at %mayapy%
    goto main
) ELSE (
    ECHO The specified Maya Python path is not valid.
    ECHO Please enter the full path to the mayapy executable.
    ECHO Hint: Typically located in the Autodesk Maya install directory, under the 'bin' folder.
    set /p mayapy="Enter full mayapy path: "
    goto validateMayapyPath
)


:main
ECHO\
ECHO What do you want to do?
ECHO	1. Install package
ECHO	2. Update package
ECHO	3. Uninstall package
ECHO	4. Get package info
ECHO	5. List installed packages
ECHO	6. Give Write Permission
ECHO	7. Backup
ECHO	8. Restore
ECHO	9. Exit

CHOICE /C:123456789

IF ERRORLEVEL 9 goto end
IF ERRORLEVEL 8 goto restore
IF ERRORLEVEL 7 goto backup
IF ERRORLEVEL 6 goto admin
IF ERRORLEVEL 5 goto list
IF ERRORLEVEL 4 goto info
IF ERRORLEVEL 3 goto uninstall
IF ERRORLEVEL 2 goto update
IF ERRORLEVEL 1 goto install


:install
ECHO\
ECHO What is the name of the package you want to INSTALL?
set /p module=
ECHO\
%mayapy% -m pip install %module%
goto main


:uninstall
ECHO\
ECHO What is the name of the package you want to UNINSTALL?
set /p module=
ECHO\
%mayapy% -m pip uninstall %module% -y
goto main


:list
ECHO\
%mayapy% -m pip list
goto main


:update
ECHO\
ECHO Which package do you want to UPDATE?
set /p module=
ECHO\
%mayapy% -m pip install %module% --upgrade
goto main


:info
ECHO\
ECHO Which specific package do you want info on?
set /p module=
ECHO\
%mayapy% -m pip show %module%
goto main


:backup
ECHO\
ECHO This operation will create a requirements file, which lists all the Python packages and their versions that are currently installed in your system. This file can be used later to recreate the same environment, including the same versions of packages.
ECHO\
%mayapy% -m pip freeze > requirements.txt
ECHO The backup file `requirements.txt` was saved in the location of this script. (%cd%)
goto main


:restore
ECHO\
IF NOT EXIST requirements.txt (
    powershell -Command "Write-Host 'requirements.txt not found. Please ensure it exists in the current directory.' -ForegroundColor Red"
    goto main
)

ECHO The following packages will be restored:
ECHO -----------------------------------
powershell -Command "Get-Content requirements.txt | Write-Host -ForegroundColor Green"
ECHO -----------------------------------
ECHO\
ECHO Do you want to proceed with the restore operation? (Y/N)
set /p user_choice=
IF /I "%user_choice%"=="Y" (
    %mayapy% -m pip install -r requirements.txt
) ELSE (
    ECHO Restore operation cancelled.
)
goto main


:admin
fltmc >nul 2>nul || set _=^"set _ELEV=1^& cd /d """%cd%"""^& "%~f0" %* ^"&&((if "%_ELEV%"=="" ((powershell -nop -c start cmd -args '/d/x/s/v:off/r',$env:_ -verb runas >nul 2>nul) || (mshta vbscript:execute^("createobject(""shell.application"").shellexecute(""cmd"",""/d/x/s/v:off/r ""&createobject(""WScript.Shell"").Environment(""PROCESS"")(""_""),,""runas"",1)(window.close)"^) >nul 2>nul)))& exit /b)
goto main


:end
ECHO Exiting...
rem cmd /k
