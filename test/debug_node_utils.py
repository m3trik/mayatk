import sys

try:
    from PySide2.QtWidgets import QApplication

    if not QApplication.instance():
        app = QApplication(sys.argv)
except ImportError:
    pass

import pymel.core as pm
import os
import tempfile
from mayatk.node_utils._node_utils import NodeUtils


def test():
    temp_dir = tempfile.mkdtemp()
    test_path = os.path.join(temp_dir, "test_texture.png").replace("\\", "/")
    with open(test_path, "w") as f:
        f.write("dummy")

    file_node = pm.shadingNode("file", asTexture=True, name="attr_test_file")
    pm.setAttr(file_node.fileTextureName, test_path)

    # Check if NodeUtils gets it
    attrs = NodeUtils.get_node_attributes(file_node, exc_defaults=True)
    print(f"Attributes keys: {attrs.keys()}")
    print(f"fileTextureName in attrs: {'fileTextureName' in attrs}")
    if "fileTextureName" in attrs:
        print(f"Value: {attrs['fileTextureName']}")

    pm.delete(file_node)
    import shutil

    shutil.rmtree(temp_dir)


test()
