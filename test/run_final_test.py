#!/usr/bin/env python
"""Comprehensive test of mayatk lazy loading architecture."""
import socket
import time

test_code = """
output_file = r'O:\\Cloud\\Code\\_scripts\\mayatk\\test\\maya_final_test.txt'
try:
    # Reload mayatk
    from pythontk.core_utils.module_reloader import reload_package
    reloaded = reload_package('mayatk', import_missing=False, verbose=0)
    
    import mayatk
    
    with open(output_file, 'w') as f:
        f.write("="*70 + "\\n")
        f.write("MAYATK LAZY LOADING TEST - FINAL")
        f.write("\\n" + "="*70 + "\\n\\n")
        
        f.write(f"Mayatk version: {mayatk.__version__}\\n")
        f.write(f"Modules reloaded: {len(reloaded)}\\n\\n")
        
        # Test lazy-loaded classes
        tests = [
            ("MeshDiagnostics", "Diagnostics class from core_utils.diagnostics.mesh"),
            ("AnimCurveDiagnostics", "Diagnostics class from core_utils.diagnostics.animation"),
            ("CoreUtils", "Legacy module class"),
            ("Selection", "Edit utils class"),
            ("Components", "Core utils class"),
            ("openPorts", "Function from env_utils.command_port"),
        ]
        
        f.write("Lazy-loaded attributes:\\n")
        f.write("-" * 70 + "\\n")
        success_count = 0
        for name, description in tests:
            try:
                obj = getattr(mayatk, name)
                obj_type = type(obj).__name__
                f.write(f"  SUCCESS {name:25s} ({obj_type})\\n")
                f.write(f"          {description}\\n")
                success_count += 1
            except AttributeError as e:
                f.write(f"  FAILED  {name:25s}\\n")
                f.write(f"          {e}\\n")
        
        f.write("\\n" + "="*70 + "\\n")
        f.write(f"Results: {success_count}/{len(tests)} passed\\n")
        f.write("="*70 + "\\n\\n")
        
        # Architecture verification
        f.write("Architecture Verification:\\n")
        f.write("-" * 70 + "\\n")
        
        # Check that subpackage __init__ is minimal
        from mayatk.core_utils import diagnostics
        import inspect
        diag_source = inspect.getsourcefile(diagnostics)
        with open(diag_source, 'r') as sf:
            diag_lines = sf.readlines()
        non_comment_lines = [l for l in diag_lines if l.strip() and not l.strip().startswith('#')]
        f.write(f"  diagnostics/__init__.py: {len(non_comment_lines)} non-comment lines\\n")
        f.write(f"  (Should be minimal - all loading via root __init__)\\n")
        
        f.write("\\n" + "="*70 + "\\n")
        f.write("TEST COMPLETE - ALL SYSTEMS OPERATIONAL\\n")
        f.write("="*70 + "\\n")
        
except Exception as e:
    import traceback
    with open(output_file, 'w') as f:
        f.write("EXCEPTION:\\n")
        f.write(traceback.format_exc())
"""

print("\n" + "=" * 70)
print("Running comprehensive mayatk lazy loading test...")
print("=" * 70 + "\n")

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(("localhost", 7002))
sock.sendall(test_code.encode("utf-8"))
sock.close()

time.sleep(3)

try:
    with open(r"O:\Cloud\Code\_scripts\mayatk\test\maya_final_test.txt", "r") as f:
        result = f.read()
        print(result)

        if "ALL SYSTEMS OPERATIONAL" in result and "6/6 passed" in result:
            print("\n" + "=" * 70)
            print("SUCCESS: All lazy loading tests passed!")
            print("=" * 70)
        else:
            print("\n" + "=" * 70)
            print("PARTIAL SUCCESS: Some tests failed")
            print("=" * 70)
except FileNotFoundError:
    print("ERROR: Test output file not created")
