@ECHO Off


ECHO/
rem ECHO Maya version? (ex. 2023)
rem set /p version=
set version=2023
set mayapy="%programfiles%\Autodesk\Maya%version%\bin\mayapy.exe"
echo %mayapy% && %mayapy% --version
echo/


rem set PYTHONPATH=
%mayapy% -c "from mayatk import append_maya_paths; append_maya_paths(str(%version%))"
%mayapy% -c "import maya.standalone; maya.standalone.initialize(name='python')"
rem %mayapy% -m pip install pymel~=1.3.0a


%mayapy% -c "from test import core_test;  core_test.unittest.main(exit=False)"
%mayapy% -c "from test import node_test;  node_test.unittest.main(exit=False)"
%mayapy% -c "from test import cmpt_test;  cmpt_test.unittest.main(exit=False)"
%mayapy% -c "from test import edit_test;  edit_test.unittest.main(exit=False)"
%mayapy% -c "from test import xform_test; xform_test.unittest.main(exit=False)"
%mayapy% -c "from test import rig_test;   rig_test.unittest.main(exit=False)"


PAUSE