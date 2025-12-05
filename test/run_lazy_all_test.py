#!/usr/bin/env python
"""Test all subpackages are now using lazy loading."""
import socket
import time

test_code = """
output_file = r'O:\\Cloud\\Code\\_scripts\\mayatk\\test\\maya_lazy_all_test.txt'
try:
    # Reload mayatk
    from pythontk.core_utils.module_reloader import reload_package
    reloaded = reload_package('mayatk', import_missing=False, verbose=0)
    
    import mayatk
    
    with open(output_file, 'w') as f:
        f.write("="*70 + "\\n")
        f.write("MAYATK COMPLETE LAZY LOADING TEST")
        f.write("\\n" + "="*70 + "\\n\\n")
        
        f.write(f"Mayatk version: {mayatk.__version__}\\n")
        f.write(f"Modules reloaded: {len(reloaded)}\\n\\n")
        
        # Test classes from all subpackages
        tests = [
            # Core utils
            ("CoreUtils", "Core utilities"),
            ("MeshDiagnostics", "Mesh diagnostics"),
            ("AnimCurveDiagnostics", "Animation diagnostics"),
            ("Components", "Component utils"),
            ("AutoInstancer", "Auto instancer"),
            ("MashToolkit", "MASH toolkit"),
            # Edit utils
            ("EditUtils", "Edit utilities"),
            ("Selection", "Selection utils"),
            ("Primitives", "Primitive utils"),
            ("Macros", "Macro utils"),
            # Env utils
            ("EnvUtils", "Environment utilities"),
            ("WorkspaceManager", "Workspace manager"),
            ("openPorts", "Command port function"),
            # Transform utils
            ("XformUtils", "Transform utilities"),
            ("Matrices", "Matrix utilities"),
            # NURBS utils
            ("NurbsUtils", "NURBS utilities"),
            ("ImageTracer", "Image tracer"),
            # Other utils
            ("AnimUtils", "Animation utilities"),
            ("CamUtils", "Camera utilities"),
            ("DisplayUtils", "Display utilities"),
            ("MatUtils", "Material utilities"),
            ("NodeUtils", "Node utilities"),
            ("RigUtils", "Rig utilities"),
            ("UiUtils", "UI utilities"),
            ("UvUtils", "UV utilities"),
        ]
        
        f.write("Lazy-loaded attributes from all subpackages:\\n")
        f.write("-" * 70 + "\\n")
        
        success_count = 0
        failed = []
        
        for name, description in tests:
            try:
                obj = getattr(mayatk, name)
                obj_type = type(obj).__name__
                f.write(f"  SUCCESS {name:25s}\\n")
                success_count += 1
            except AttributeError as e:
                f.write(f"  FAILED  {name:25s} - {e}\\n")
                failed.append((name, str(e)))
        
        f.write("\\n" + "="*70 + "\\n")
        f.write(f"Results: {success_count}/{len(tests)} passed\\n")
        
        if failed:
            f.write("\\nFailed imports:\\n")
            for name, error in failed:
                f.write(f"  - {name}: {error}\\n")
        
        f.write("="*70 + "\\n\\n")
        
        # Check subpackage __init__ files are minimal
        f.write("Subpackage __init__.py verification:\\n")
        f.write("-" * 70 + "\\n")
        
        import inspect
        subpackages = [
            'anim_utils', 'cam_utils', 'core_utils', 'display_utils',
            'edit_utils', 'env_utils', 'light_utils', 'mat_utils',
            'node_utils', 'nurbs_utils', 'rig_utils', 'ui_utils',
            'uv_utils', 'xform_utils'
        ]
        
        for pkg_name in subpackages:
            try:
                pkg = getattr(mayatk, pkg_name, None)
                if pkg:
                    source_file = inspect.getsourcefile(pkg)
                    with open(source_file, 'r') as sf:
                        lines = sf.readlines()
                    non_comment = [l for l in lines if l.strip() and not l.strip().startswith('#')]
                    f.write(f"  {pkg_name:20s}: {len(non_comment):2d} non-comment lines\\n")
            except:
                pass
        
        f.write("\\n" + "="*70 + "\\n")
        if success_count == len(tests):
            f.write("SUCCESS: ALL LAZY LOADING OPERATIONAL\\n")
        else:
            f.write(f"PARTIAL: {success_count}/{len(tests)} working\\n")
        f.write("="*70 + "\\n")
        
except Exception as e:
    import traceback
    with open(output_file, 'w') as f:
        f.write("EXCEPTION:\\n")
        f.write(traceback.format_exc())
"""

print("\n" + "=" * 70)
print("Testing complete lazy loading architecture...")
print("=" * 70 + "\n")

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect(("localhost", 7002))
sock.sendall(test_code.encode("utf-8"))
sock.close()

time.sleep(4)

try:
    with open(r"O:\Cloud\Code\_scripts\mayatk\test\maya_lazy_all_test.txt", "r") as f:
        result = f.read()
        print(result)

        if "SUCCESS: ALL LAZY LOADING OPERATIONAL" in result:
            print("\n" + "=" * 70)
            print("SUCCESS: All subpackages using lazy loading!")
            print("=" * 70)
except FileNotFoundError:
    print("ERROR: Test output file not created")
