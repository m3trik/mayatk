@ECHO Off


ECHO/
rem ECHO Maya version? (ex. 2023)
rem set /p version=
set version=2023
set mayapy="%programfiles%\Autodesk\Maya%version%\bin\mayapy.exe"
echo %mayapy% && %mayapy% --version
echo/


rem set PYTHONPATH=
rem %mayapy% -c "from mayatk import appendMayaPaths; appendMayaPaths(str(%version%))"
rem %mayapy% -c "import maya.standalone; maya.standalone.initialize(name='python')"
rem %mayapy% -m pip install pymel~=1.3.0a


%mayapy% -c "from test import Core_test;  Core_test.unittest.main(exit=False)"
%mayapy% -c "from test import Node_test;  Node_test.unittest.main(exit=False)"
%mayapy% -c "from test import Cmpt_test;  Cmpt_test.unittest.main(exit=False)"
%mayapy% -c "from test import Edit_test;  Edit_test.unittest.main(exit=False)"
%mayapy% -c "from test import Xform_test; Xform_test.unittest.main(exit=False)"
%mayapy% -c "from test import Rig_test;   Rig_test.unittest.main(exit=False)"


PAUSE