with open("tests/unit/test_git_consolidation.py", "r") as f:
    content = f.read()

content = content.replace("import typing\n", "import typing\n")

# Revert my bad fix attempt and fix RUF012 properly
content = content.replace("ALL_PUBLIC_METHODS: typing.ClassVar[list[str]] =", "ALL_PUBLIC_METHODS =")

lines = content.splitlines()
import_added = False
new_lines = []
for line in lines:
    if line.startswith("import pytest") and not import_added:
        new_lines.append(line)
        new_lines.append("from typing import ClassVar")
        import_added = True
    elif "ALL_PUBLIC_METHODS =" in line:
        new_lines.append(line.replace("ALL_PUBLIC_METHODS =", "ALL_PUBLIC_METHODS: ClassVar[list[str]] ="))
    elif "/tmp/test" in line:
        new_lines.append(line.replace("/tmp/test", "/tmp/test_dir"))
    else:
        new_lines.append(line)

content = "\n".join(new_lines) + "\n"

with open("tests/unit/test_git_consolidation.py", "w") as f:
    f.write(content)
