# Bridge Tool - Cleanup Feature

## Overview
The Bridge tool in mayatk has been enhanced with a new "Cleanup" option that allows users to automatically clean up child curves created during bridge operations when the curve type is set to "Curve".

## Feature Details

### New UI Element
- **Checkbox**: "Cleanup"
- **Location**: Added between the Twist parameter and Create button in the Bridge UI
- **Tooltip**: "Clean up child curves and deformer history when using curve bridge type."

### Functionality
When the "Cleanup" checkbox is enabled and the bridge type is set to "Curve" (curveType = 2):

1. **Before Bridge Operation**: The tool identifies the mesh nodes from the selected edges
2. **Bridge Operation**: Performs the normal polyBridgeEdge operation with curve creation
3. **Post-Operation Cleanup**: 
   - Deletes deformer history on the mesh nodes using `pm.delete(mesh_node, constructionHistory=True)`
   - Finds and deletes any child curves created by the bridge operation
   - Provides console feedback about the cleanup process

### New Methods Added

#### `Bridge.get_child_curves_from_bridge(mesh_nodes)`
Finds child curves created by polyBridgeEdge operations on mesh nodes.

**Parameters:**
- `mesh_nodes` (list): List of mesh transform nodes to check for child curves

**Returns:**
- `list`: List of curve nodes that are children of the mesh nodes

#### `Bridge.cleanup_bridge_curves_and_history(mesh_nodes)`
Clean up child curves and deformer history from mesh nodes.

**Parameters:**
- `mesh_nodes` (list): List of mesh transform nodes to clean up

**Process:**
1. Finds child curves using `get_child_curves_from_bridge()`
2. Deletes construction history on each mesh node
3. Deletes the identified child curves
4. Provides console output for each step

### Usage Workflow

1. **Select Edges**: Select the edges you want to bridge
2. **Set Bridge Type**: Choose "Type: Curve" from the dropdown
3. **Enable Cleanup**: Check the "Cleanup" checkbox
4. **Configure Parameters**: Set divisions, smoothing angle, offset, taper, and twist as needed
5. **Execute**: Click the "Create" button

The bridge operation will complete and automatically clean up the child curve and deformer history.

### Code Integration

The new functionality integrates seamlessly with the existing preview system and UI connections:

```python
# Connect the cleanup checkbox to preview refresh
self.sb.connect_multi(self.ui, "chk001", "toggled", self.preview.refresh)
```

The checkbox state is checked during the `perform_operation()` method:

```python
# Clean up child curves if option is enabled and curve type is selected
if self.ui.chk001.isChecked() and kwargs["curveType"] == 2 and mesh_nodes:
    Bridge.cleanup_bridge_curves_and_history(mesh_nodes)
```

### Design Philosophy

- **Direct Access**: The code directly accesses UI elements (`self.ui.chk001`) rather than using defensive `hasattr()` checks that can mask bugs
- **Clear Naming**: Simple "Cleanup" terminology instead of verbose descriptions
- **Fail Fast**: If the UI element doesn't exist, the code will fail immediately, making issues apparent rather than silently ignoring them

### Benefits

1. **Workflow Efficiency**: Eliminates the need for manual cleanup of child curves
2. **Clean Geometry**: Automatically removes deformer history that could cause issues
3. **User Control**: Optional feature that doesn't interfere with existing workflows
4. **Feedback**: Clear console messages inform users of cleanup operations

This enhancement streamlines the bridge workflow when using curve-based bridges by automatically handling the common post-operation cleanup tasks.
