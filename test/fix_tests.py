import re

file_path = r"o:\Cloud\Code\_scripts\mayatk\test\test_auto_instancer.py"
limit_line = 1279

with open(file_path, "r") as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    if i + 1 >= limit_line:
        new_lines.append(line)
        continue

    if "AutoInstancer(" in line and "is_static" not in line:
        # Check if it's a single line call
        if ")" in line:
            # Regex to find arguments
            match = re.search(r"AutoInstancer\((.*?)\)", line)
            if match:
                args = match.group(1).strip()
                if args:
                    new_line = line.replace(args, args + ", is_static=False")
                else:
                    new_line = line.replace(
                        "AutoInstancer()", "AutoInstancer(is_static=False)"
                    )
                new_lines.append(new_line)
            else:
                new_lines.append(line)  # Should not happen if ) is present
        else:
            # Multi-line call? I haven't seen any in the read_file output, but let's be safe.
            # If no closing parenthesis, it's multi-line.
            # I'll just skip modifying multi-line calls for now to avoid breakage.
            # Or I can assume they are rare.
            new_lines.append(line)
    else:
        new_lines.append(line)

with open(file_path, "w") as f:
    f.writelines(new_lines)

print("Modified test_auto_instancer.py")
