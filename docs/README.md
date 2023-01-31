### mayattk is a collection of backend utilities for Autodesk Maya.

---

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
```
from mayatk import Node
Node.isLocator(<obj>)
```
###### You can also import a function directly.
```
from mayatk.Node import isGroup
isGroup(<obj>)
```