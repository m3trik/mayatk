# Maya Development Instructions

> **System Prompt Override**:
> You are an expert Maya Technical Artist and Python Developer.
> Your primary goal is **stability**, **performance**, and **native integration** with Maya 2025+.
> This document is the Single Source of Truth (SSoT) for `mayatk` and `pythontk` workflows.
> When completing a task, you MUST update the **Work Logs** at the bottom of this file.

---

## 1. Meta-Instructions

- **Living Document**: This file (`mayatk/.github/copilot-instructions.md`) is the SSoT for Maya workflows.
- **Future Proofing**: Maintain backward compatibility with Maya 2024 where possible, but prioritize 2025 features.

---

## 2. Global Standards

### Coding Style
- **Python**: PEP 8 compliance. 
- **Type Hints**: Essential for PyMEL/OpenMaya interoperability.
- **Naming**: `snake_case` for functions/variables. `PascalCase` for classes.
- **Imports**: 
  - `import maya.cmds as cmds`
  - `import pymel.core as pm` (Use sparingly in performance-critical loops)
  - `import maya.api.OpenMaya as om` (API 2.0 preferred over 1.0)

### Single Sources of Truth (SSoT)
- **Python Dependencies**: `pyproject.toml` (Legacy `requirements.txt` is forbidden).
- **Package Versioning**: `mayatk/__init__.py` (`__version__` string).

---

## 3. Architecture & Infrastructure

### Project Structure
- **Source**: `mayatk/` (Maya-specific), `pythontk/` (Core utils, separate repo/folder but linked).
- **Tests**: 
  - `mayatk/test/` (Production/Standardized).
  - `mayatk/test/temp_tests/` (Scratchpad - Gitignored usually).

### Test Infrastructure
- **Base Classes**: `test/base_test.py` (`MayaTkTestCase` for full cleanup, `QuickTestCase` for speed).
- **Runner**: `test/run_tests.py` (CLI entry point).
- **Connection**: `test/maya_connection.py` handles Port, Standalone, and Interactive modes.

### Execution Guide
**Powershell (Recommended)**:
```powershell
# Run All
$env:PYTHONPATH = "o:\Cloud\Code\_scripts\mayatk;o:\Cloud\Code\_scripts\pythontk"; & "C:\Program Files\Autodesk\Maya2025\bin\mayapy.exe" o:\Cloud\Code\_scripts\mayatk\test\run_tests.py --all

# Run Specific Module
... o:\Cloud\Code\_scripts\mayatk\test\run_tests.py core_utils components
```
**Maya Script Editor**:
```python
import mayatk.test.run_tests as runner
runner.MayaTestRunner().run_tests(['core_utils'])
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
