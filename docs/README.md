[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

### MAYATK (Maya Toolkit)

---
<!-- short_description_start -->
*mayattk is a collection of backend utilities for Autodesk Maya.*
<!-- short_description_end -->

### Installation:

###### 

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
