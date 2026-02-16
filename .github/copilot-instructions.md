# Maya Development Instructions

> **System Prompt Override**:
> You are an expert Maya Technical Artist and Python Developer.
> Your primary goal is **stability**, **performance**, and **native integration** with Maya 2025+.
>
> **Global Standards**: For general workflow, testing, and coding standards, refer to the [Main Copilot Instructions](../../.github/copilot-instructions.md).
>
> **Work Logs**: When completing a task, you MUST update the **Work Logs** at the bottom of this file.

---

## 1. Meta-Instructions

- **Living Document**: This file (`mayatk/.github/copilot-instructions.md`) is the SSoT for Maya specific workflows.
- **Future Proofing**: Maintain backward compatibility with Maya 2024 where possible, but prioritize 2025 features.
- **Style**: Use Type Hints essential for PyMEL/OpenMaya interoperability.
- **Imports**: 
  - `import maya.cmds as cmds`
  - `import pymel.core as pm` (Use sparingly in performance-critical loops)
  - `import maya.api.OpenMaya as om` (API 2.0 preferred over 1.0)

## 2. Architecture & Infrastructure

### Project Structure
- **Source**: `mayatk/` (Maya-specific), `pythontk/` (Core utils, separate repo/folder but linked).
- **Tests**: 
  - `mayatk/test/` (Production/Standardized).
  - `mayatk/test/temp_tests/` (Scratchpad - Gitignored usually).

### Test Infrastructure

> **CRITICAL — Maya Runtime Required**:
> All mayatk tests depend on `pymel` / `maya.cmds` and **cannot** be run with
> `pytest`, the workspace `.venv`, or any standard Python interpreter.
> They **must** be executed through one of the methods below.
>
> **Syntax-only validation** (no Maya needed):
> ```powershell
> .\.venv\Scripts\python.exe -c "import ast, os; c=0; [((ast.parse(open(os.path.join(r,f),encoding='utf-8').read()), c:=c+1) if f.endswith('.py') else None) for r,_,fs in os.walk(r'mayatk\mayatk') for f in fs]; print(f'{c} files OK')"
> ```
> Use this to verify edits parse correctly when Maya is unavailable.

> **Testing Workflow**: Follow the **Issue-Driven TDD** workflow defined in the main [copilot-instructions.md](../../.github/copilot-instructions.md#testing-workflow-issue-driven-tdd).

- **Base Classes**: `test/base_test.py` (`MayaTkTestCase` for full cleanup, `QuickTestCase` for speed).
- **Runner**: `test/run_tests.py` (CLI entry point).
- **Connection**: `test/maya_connection.py` handles Port, Standalone, and Interactive modes.

### Running Tests

**1. mayapy (Recommended — headless)**:
```powershell
$env:PYTHONPATH = "o:\Cloud\Code\_scripts\mayatk;o:\Cloud\Code\_scripts\pythontk"

# Run all tests
& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" o:\Cloud\Code\_scripts\mayatk\test\run_tests.py --all

# Run a specific module's tests
& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" o:\Cloud\Code\_scripts\mayatk\test\run_tests.py core_utils components

# List available test modules
& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" o:\Cloud\Code\_scripts\mayatk\test\run_tests.py --list
```

**2. Maya Script Editor (interactive)**:
```python
import mayatk.test.run_tests as runner
runner.MayaTestRunner().run_tests(['core_utils'])
```

**3. Maya Command Port (remote)**:
Requires Maya running with `cmds.commandPort(name=":7002", sourceType="python")` open.
```powershell
& "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" o:\Cloud\Code\_scripts\mayatk\test\run_tests.py --all
```

---

## 4. Work Logs & History (2025-2026)

### Maya Development (2025)
- [x] **Test Infrastructure** — Unified `run_tests.py` runner, standardized `test_*.py` files.
- [x] **Maya Connection** — Robust support for Standalone, Port, and Interactive modes.
- [x] **Game Shader** — Refactored to `GameShader`, extracted `MaterialUpdater`.
- [x] **Texture Map Factory** — Implemented in-memory pipeline (`PIL`), dynamic `MapRegistry`.
- [x] **Animation Tools** — Recursive scaling, overlap prevention strategies, absolute/relative modes.
- [x] **AutoInstancer** — Deep hierarchy support, robust PCA alignment, `InstancingStrategy` implementation.
- [x] **Scene Exporter** — Transitioned critical paths from PyMEL to `maya.cmds` for 5x speedup.
