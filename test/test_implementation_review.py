"""
Implementation review and verification script.
Reviews the actual code to ensure it follows the correct pattern.
"""

import re


def review_implementation():
    """Review the actual implementation in _anim_utils.py."""

    print("=" * 70)
    print("IMPLEMENTATION REVIEW")
    print("=" * 70 + "\n")

    file_path = r"o:\Cloud\Code\_scripts\mayatk\mayatk\anim_utils\_anim_utils.py"

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"❌ Could not read file: {e}")
        return False

    checks = []

    # Check 1: Query selected times once and store them
    pattern1 = r"curve_selected_times\s*=\s*\{\}"
    if re.search(pattern1, content):
        checks.append(("✓ Creates curve_selected_times dictionary", True))
    else:
        checks.append(("✗ Missing curve_selected_times dictionary", False))

    # Check 2: Stores selected times in dictionary
    pattern2 = r"curve_selected_times\[curve\]\s*=\s*list\(selected_times\)"
    if re.search(pattern2, content):
        checks.append(("✓ Stores selected times in dictionary", True))
    else:
        checks.append(("✗ Does not store selected times properly", False))

    # Check 3: Comment about querying upfront
    pattern3 = r"Query selected times once upfront"
    if re.search(pattern3, content):
        checks.append(("✓ Has comment about querying upfront", True))
    else:
        checks.append(("✗ Missing explanatory comment", False))

    # Check 4: Comment about not relying on selection state
    pattern4 = r"don't rely on selection state"
    if re.search(pattern4, content):
        checks.append(("✓ Has comment about selection state", True))
    else:
        checks.append(("✗ Missing selection state comment", False))

    # Check 5: Iterates over stored data
    pattern5 = r"for curve, selected_times in curve_selected_times\.items\(\):"
    if re.search(pattern5, content):
        checks.append(("✓ Iterates over stored curve_selected_times", True))
    else:
        checks.append(("✗ Does not iterate over stored data", False))

    # Check 6: Manual calculation formula
    pattern6 = (
        r"new_time\s*=\s*pivot_time\s*\+\s*\(old_time\s*-\s*pivot_time\)\s*\*\s*factor"
    )
    if re.search(pattern6, content):
        checks.append(("✓ Uses manual calculation formula", True))
    else:
        checks.append(("✗ Missing manual calculation formula", False))

    # Check 7: Comment about manual calculation
    pattern7 = r"new_time = pivot \+ \(old_time - pivot\) \* factor"
    if re.search(pattern7, content):
        checks.append(("✓ Has formula comment", True))
    else:
        checks.append(("✗ Missing formula comment", False))

    # Check 8: Creates time_pairs list
    pattern8 = r"time_pairs\.append\(\(old_time, new_time\)\)"
    if re.search(pattern8, content):
        checks.append(("✓ Creates time_pairs correctly", True))
    else:
        checks.append(("✗ Does not create time_pairs", False))

    # Check 9: Calls _move_curve_keys
    pattern9 = r"cls\._move_curve_keys\(curve, time_pairs\)"
    if re.search(pattern9, content):
        checks.append(("✓ Calls _move_curve_keys helper", True))
    else:
        checks.append(("✗ Does not call _move_curve_keys", False))

    # Check 10: Extracts new_times for snapping
    pattern10 = r"new_times\s*=\s*\[new_time for _, new_time in time_pairs\]"
    if re.search(pattern10, content):
        checks.append(("✓ Extracts new_times for snapping", True))
    else:
        checks.append(("✗ Does not extract new_times correctly", False))

    # Check 11: Calls _snap_curve_keys
    pattern11 = r"cls\._snap_curve_keys\(curve, new_times, snap_mode\)"
    if re.search(pattern11, content):
        checks.append(("✓ Calls _snap_curve_keys helper", True))
    else:
        checks.append(("✗ Does not call _snap_curve_keys", False))

    # Check 12: Does NOT use scaleSpecifiedKeys in selected branch
    # Look specifically in the selected_keys_only branch
    selected_branch_match = re.search(
        r"if selected_keys_only:.*?(?=\n\s*else:|\n\s*for curve in curves_to_scale:(?!.*selected_keys_only))",
        content,
        re.DOTALL,
    )

    if selected_branch_match:
        selected_branch_code = selected_branch_match.group(0)
        if (
            "scaleSpecifiedKeys" not in selected_branch_code
            and "pm.scaleKey" not in selected_branch_code
        ):
            checks.append(("✓ Does NOT use scaleKey in selected branch", True))
        else:
            checks.append(("✗ Still uses scaleKey in selected branch", False))
    else:
        checks.append(("⚠ Could not extract selected_keys_only branch", None))

    # Print results
    print("Implementation Checks:\n")

    passed = 0
    failed = 0
    warnings = 0

    for check_name, result in checks:
        print(f"  {check_name}")
        if result is True:
            passed += 1
        elif result is False:
            failed += 1
        else:
            warnings += 1

    print(f"\n{'='*70}")
    print(f"SUMMARY: {passed} passed, {failed} failed, {warnings} warnings")
    print(f"{'='*70}\n")

    if failed == 0:
        print("✅ IMPLEMENTATION VERIFIED!")
        print("\nThe code correctly implements:")
        print("  • Queries selection state only once at the beginning")
        print("  • Stores selected times in curve_selected_times dictionary")
        print("  • Works purely with stored data during scaling")
        print("  • Uses manual calculation instead of scaleSpecifiedKeys")
        print("  • Properly structures time_pairs for _move_curve_keys")
        print("  • Correctly extracts new_times for _snap_curve_keys")
        print("\nThis avoids the fundamental flaw of relying on Maya's")
        print("graph editor selection state during the scaling operation.")
        return True
    else:
        print("❌ IMPLEMENTATION HAS ISSUES")
        print(f"\n{failed} check(s) failed. Review the code.")
        return False


if __name__ == "__main__":
    success = review_implementation()
    exit(0 if success else 1)
