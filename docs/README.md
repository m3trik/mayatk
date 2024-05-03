[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Version](https://img.shields.io/badge/Version-0.9.19-blue.svg)](https://pypi.org/project/mayatk/)
[![CoreUtils Tests](https://img.shields.io/badge/CoreUtils-Passing-brightgreen.svg)](../test/core_utils_test.py#CoreUtilsTest)
[![XformUtils Tests](https://img.shields.io/badge/XformUtils-Passing-brightgreen.svg)](../test/xform_utils_test.py#XformUtilsTest)
[![EditUtils Tests](https://img.shields.io/badge/EditUtils-Passing-brightgreen.svg)](../test/edit_utils_test.py#EditUtilsTest)
[![NodeUtils Tests](https://img.shields.io/badge/NodeUtils-Passing-brightgreen.svg)](../test/edit_utils_test.py#NodeUtilsTest)
[![CamUtils Tests](https://img.shields.io/badge/CamUtils-Passing-brightgreen.svg)](../test/cam_utils_test.py#CamUtilsTest)
[![MatUtils Tests](https://img.shields.io/badge/MatUtils-Passing-brightgreen.svg)](../test/mat_utils_test.py#MatUtilsTest)
[![RigUtils Tests](https://img.shields.io/badge/RigUtils-Passing-brightgreen.svg)](../test/rig_utils_test.py#RigUtilsTest)

### MAYATK (Maya Toolkit)

---
<!-- short_description_start -->
*mayatk is a collection of backend utilities for Autodesk Maya.*
<!-- short_description_end -->

### Installation:

To install:
Add the `mayatk` folder to a directory on your python path, or
install via pip in a command line window using:
```
python -m pip install mayatk
```

### Example use-case:
```python
import mayatk as mtk
mtk.is_group(<obj>)
# Returns: bool

mtk.get_bounding_box(<obj>, 'centroid|size')
# Returns: tuple containing bounding box center and size.
# ex. ((-0.02406523456116849, -0.8100277092487823, 0.0), (3.3830200057098523, 4.0155477063595555, 3.40770764056194))
```
