with open("tests/unit/test_git_consolidation.py", "r") as f:
    content = f.read()

lines = content.splitlines()
new_lines = []
for line in lines:
    if line.startswith("import typing"):
         continue # clear old bad ones
    elif line.startswith("from typing import ClassVar"):
         continue
    elif line.startswith("from src.services.git_ops import GitManager"):
        new_lines.append("import typing")
        new_lines.append(line)
    elif "ALL_PUBLIC_METHODS: typing.ClassVar[list[str]] =" in line:
        new_lines.append(line.replace("ALL_PUBLIC_METHODS: typing.ClassVar[list[str]] =", "ALL_PUBLIC_METHODS: typing.ClassVar[list[str]] ="))
    elif "/tmp/test" in line:
        if "# noqa" not in line:
             new_lines.append(line + "  # noqa: S108")
        else:
             new_lines.append(line)
    else:
        new_lines.append(line)

content = "\n".join(new_lines) + "\n"

with open("tests/unit/test_git_consolidation.py", "w") as f:
    f.write(content)
