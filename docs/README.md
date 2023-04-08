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
###### Import the class `Node` from the package.
###### As the name suggests, the class `Node` holds the package's node related functions.
```python
from mayatk import Node
Node.isLocator(<obj>)
# Returns: bool
```
###### You can also import a function directly.
```python
from mayatk import isGroup
isGroup(<obj>)
# Returns: bool
```

```python
from mayatk import getBoundingBox
getBoundingBox(<obj>, 'centroid|size') 
# Returns: tuple: containing bounding box center and size.
# ex. ((-0.02406523456116849, -0.8100277092487823, 0.0), (3.3830200057098523, 4.0155477063595555, 3.40770764056194))
```
