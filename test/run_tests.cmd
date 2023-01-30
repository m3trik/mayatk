@ECHO Off


ECHO/
rem ECHO Maya version? (ex. 2022)
rem set /p ver=
set version=2023
set mayapy="%programfiles%\Autodesk\Maya%version%\bin\mayapy.exe"

rem %mayapy% -c "import maya.standalone; maya.standalone.initialize(name='python')"


%mayapy% core_test.py
rem %mayapy% edit_test.py
rem %mayapy% comp_test.py
rem %mayapy% rig_test.py
rem %mayapy% xform_test.py


PAUSE