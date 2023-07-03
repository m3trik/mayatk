[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Utils Tests](https://img.shields.io/badge/Utils-Passing-brightgreen.svg)](../test/utils_test.py#UtilsTest)
[![RigUtils Tests](https://img.shields.io/badge/Utils-Passing-brightgreen.svg)](../test/rig_utils_test.py#RigUtilsTest)
[![ProjUtils Tests](https://img.shields.io/badge/Utils-Passing-brightgreen.svg)](../test/proj_utils_test.py#ProjUtilsTest)
[![NodeUtils Tests](https://img.shields.io/badge/Utils-Passing-brightgreen.svg)](../test/node_utils_test.py#NodeUtilsTest)
[![MashUtils Tests](https://img.shields.io/badge/Utils-Passing-brightgreen.svg)](../test/mash_utils_test.py#MashUtilsTest)
[![EditUtils Tests](https://img.shields.io/badge/Utils-Passing-brightgreen.svg)](../test/edit_utils_test.py#EditUtilsTest)
[![CmptUtils Tests](https://img.shields.io/badge/Utils-Passing-brightgreen.svg)](../test/cmpt_utils_test.py#CmptUtilsTest)
[![CamUtils Tests](https://img.shields.io/badge/Utils-Passing-brightgreen.svg)](../test/cam_utils_test.py#CamUtilsTest)
[![XformUtils Tests](https://img.shields.io/badge/Utils-Passing-brightgreen.svg)](../test/xform_utils_test.py#XformUtilsTest)
[![MatUtils Tests](https://img.shields.io/badge/Utils-Passing-brightgreen.svg)](../test/mat_utils_test.py#MatUtilsTest)


### MAYATK (Maya Toolkit)

---
<!-- short_description_start -->
*mayattk is a collection of backend utilities for Autodesk Maya.*
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

mtk.getBoundingBox(<obj>, 'centroid|size') 
# Returns: tuple containing bounding box center and size.
# ex. ((-0.02406523456116849, -0.8100277092487823, 0.0), (3.3830200057098523, 4.0155477063595555, 3.40770764056194))
```
