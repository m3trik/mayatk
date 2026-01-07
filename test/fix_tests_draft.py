import re
import os

file_path = r"o:\Cloud\Code\_scripts\mayatk\test\test_auto_instancer.py"

with open(file_path, "r") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if "AutoInstancer(" in line:
        # Check if is_static is already present
        if "is_static" in line:
            new_lines.append(line)
            continue

        # Check if it's a strategy test that SHOULD fail/pass based on strategy
        # The strategy tests are: test_strategy_micro_mesh, test_strategy_gpu_instance, test_strategy_standard
        # These tests rely on the default behavior (is_static=True) to trigger the strategy logic.
        # So we should NOT modify them.
        # How to detect? They are inside methods named test_strategy_...
        # But I am processing line by line. I need context.

        # Actually, simpler approach:
        # The strategy tests were just added at the end of the file.
        # I can just skip modification if the line is inside one of those methods.
        # But tracking methods is hard line-by-line.

        # Alternative: Only modify specific known patterns from the OLD tests.
        # Old tests use:
        # AutoInstancer(verbose=True)
        # AutoInstancer(check_hierarchy=True, verbose=True)
        # AutoInstancer(check_hierarchy=True)
        # AutoInstancer(check_uvs=True, verbose=True)
        # AutoInstancer(separate_combined=True, verbose=True)

        # New tests use:
        # AutoInstancer(verbose=True)  <-- Conflict! test_strategy_standard uses this.

        # Wait, test_strategy_standard EXPECTS instancing to happen (it uses a heavy mesh).
        # So adding is_static=False won't hurt it?
        # If is_static=False, it prefers GPU_INSTANCE.
        # If is_static=True, it evaluates strategy.
        # For test_strategy_standard, we WANT it to evaluate strategy and decide GPU_INSTANCE.
        # If we set is_static=False, it might skip the strategy check?
        # Let's check AutoInstancer code.
        pass

    new_lines.append(line)
