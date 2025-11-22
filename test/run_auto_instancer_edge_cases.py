"""Run AutoInstancer edge case tests in Maya."""

import unittest

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("mayatk.test.auto_instancer_test")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 70)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped)}")
    print("=" * 70)
