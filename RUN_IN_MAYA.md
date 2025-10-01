# Mirror Debug Test - Maya Instructions

## How to run this test:

1. Open Maya
2. Make sure your Python path includes the mayatk directory
3. Open Maya's Script Editor (Windows > General Editors > Script Editor)
4. Copy and paste the entire contents of `simple_mirror_test.py` into the Script Editor
5. Press Ctrl+Enter to execute

## What the test does:

1. Creates a test hemisphere at position (3, 1, 0) with rotation (15, 30, 0)
2. Tests different pivot calculation methods (object, center, manip, world)
3. Runs mirror operations with different parameters:
   - Object space X mirror (merged)
   - World space X mirror (merged) 
   - Object space X mirror at center (merged)
   - Object space -X mirror (separated)

## What to look for:

- Check if the pivot calculations return valid values (not [0,0,0])
- Verify that polyMirrorFace operations succeed without errors
- Confirm that the mirrored objects appear correctly in the viewport
- For separated mirrors, check that new objects are created with proper naming

## Expected results:

- Pivot calculations should return actual position values
- Mirror operations should complete without "No valid objects supplied" errors
- Mirrored geometry should appear at the correct positions relative to the pivot
- The test should print "SUCCESS" for each working mirror operation

## Recent fixes applied:

1. Fixed manipulator pivot queries with proper selection management
2. Enhanced object-space coordinate transformations
3. Corrected return value handling for separated vs merged mirrors
4. Added proper validation for pivot data

Run this test and paste the complete output to continue debugging if issues persist.
