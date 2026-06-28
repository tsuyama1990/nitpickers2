with open("tests/unit/test_git_consolidation.py", "r") as f:
    content = f.read()

lines = content.splitlines()
new_lines = []
for line in lines:
    if line.startswith("import pytest"):
        new_lines.append(line)
        new_lines.append("import typing")
    elif "ALL_PUBLIC_METHODS =" in line:
        new_lines.append(line.replace("ALL_PUBLIC_METHODS =", "ALL_PUBLIC_METHODS: typing.ClassVar[list[str]] ="))
    elif "/tmp/test" in line:
        new_lines.append(line + "  # noqa: S108")
    else:
        new_lines.append(line)

content = "\n".join(new_lines) + "\n"

with open("tests/unit/test_git_consolidation.py", "w") as f:
    f.write(content)
