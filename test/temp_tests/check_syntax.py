import ast
import glob
import os

os.chdir(r"o:\Cloud\Code\_scripts")
bad = []
total = 0
for f in sorted(glob.glob("mayatk/mayatk/**/*.py", recursive=True)):
    total += 1
    try:
        ast.parse(open(f, "rb").read(), f)
    except SyntaxError as e:
        bad.append(f"{f}: {e}")

print(f"{total} files checked, {len(bad)} errors")
for b in bad:
    print(b)
