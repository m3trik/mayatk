"""Bisect which ZomPack parameter override crashes RizomUV 2020.1.

Starts from the known-good pack body (no Margin / no Quality / Rotate.Step=90)
and toggles one override at a time.
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

RIZOM = Path(r"C:\Program Files\Rizom Lab\RizomUV 2020.1\Rizomuv_VS.exe")
TEST_DIR = Path(tempfile.gettempdir()) / "rzbridge_smoketest"
SOURCE_FBX = TEST_DIR / "source.fbx"


def pack_body(margin=None, quality=None, rotate_step=90, scaling=2, layout=2, depth=2):
    extras = []
    if margin is not None:
        extras.append(f"Margin={margin}")
    if quality is not None:
        extras.append(f"Quality={quality}")
    extras_str = (",\n    " + ",\n    ".join(extras)) if extras else ""
    return f"""
ZomSelect({{PrimType="Island", Select=true, ResetBefore=true}})
ZomIslandGroups({{Mode="DistributeInTilesEvenly", MergingPolicy=8322, GroupPath="RootGroup"}})
ZomPack({{
    ProcessTileSelection=false,
    RecursionDepth={depth},
    RootGroup="RootGroup",
    Scaling={{Mode={scaling}}},
    Rotate={{Step={rotate_step}}},
    Translate=true,
    LayoutScalingMode={layout}{extras_str}
}})
"""


CASES = {
    "baseline_no_extras":      pack_body(),
    "with_margin_default":     pack_body(margin="0.002"),
    "with_margin_override":    pack_body(margin="0.005"),
    "with_quality_default":    pack_body(quality=1),
    "with_quality_override":   pack_body(quality=2),
    "with_quality_max":        pack_body(quality=3),
    "rotate_step_45":          pack_body(rotate_step=45),
    "scaling_mode_1":          pack_body(scaling=1),
    "layout_scaling_1":        pack_body(layout=1),
    "recursion_depth_3":       pack_body(depth=3),
    "all_overrides":           pack_body(margin="0.005", quality=2, rotate_step=45, scaling=1, layout=1, depth=3),
}


def run(case_name: str, body: str) -> int:
    fbx = TEST_DIR / f"bisect_p_{case_name}.fbx"
    shutil.copyfile(SOURCE_FBX, fbx)
    script = f"""ZomLoad({{File={{Path="{fbx.as_posix()}", ImportGroups=true, XYZ=true, FBX=true}}}})
{body}
ZomSave({{File={{Path="{fbx.as_posix()}", UVWProps=true, FBX=true}}, __UpdateUIObjFileName=true}})
ZomQuit()
"""
    f = TEST_DIR / f"bisect_p_{case_name}.lua"
    f.write_text(script, encoding="utf-8")
    pre = fbx.stat().st_mtime
    t0 = time.time()
    proc = subprocess.run(
        [str(RIZOM), "-cfi", f.as_posix()],
        capture_output=True, text=True, timeout=120,
    )
    dt = time.time() - t0
    saved = fbx.stat().st_mtime != pre if fbx.exists() else False
    print(f"  rc={proc.returncode}  elapsed={dt:.1f}s  saved={saved}")
    return proc.returncode


if __name__ == "__main__":
    for name, body in CASES.items():
        print(f"\n=== {name} ===")
        run(name, body)
