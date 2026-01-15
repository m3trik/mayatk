import pymel.core as pm

try:
    import maya.standalone

    maya.standalone.initialize()
except:
    pass

import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))
from mayatk.node_utils._node_utils import NodeUtils

# Test
cam_trans, cam_shape = pm.camera()
print(f"Camera: {cam_trans.name()}, is_group: {NodeUtils.is_group(cam_trans.name())}")

grp = pm.group(em=True, name="TestGroup")
print(f"Group: {grp.name()}, is_group: {NodeUtils.is_group(grp.name())}")

# Test the one from error log
persp = "|persp"
if pm.objExists(persp):
    print(f"Persp: {persp}, is_group: {NodeUtils.is_group(persp)}")
else:
    print("Persp not found/standalone fresh scene")
