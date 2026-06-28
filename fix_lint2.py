with open("tests/integration/test_integration_graph.py", "r") as f:
    content = f.read()

content = content.replace("""    with (
        patch.object(settings.paths, "workspace_root", repo_path),
        patch("os.getcwd", return_value=str(repo_path)),
    ):
        # Mock the integration fixer node to resolve the issue
        with patch(
            "src.nodes.integration_fixer.IntegrationFixerNodes.integration_fixer_node"
        ) as mock_fixer:""", """    with (
        patch.object(settings.paths, "workspace_root", repo_path),
        patch("os.getcwd", return_value=str(repo_path)),
        patch("src.nodes.integration_fixer.IntegrationFixerNodes.integration_fixer_node") as mock_fixer,
    ):
        # Mock the integration fixer node to resolve the issue""")

with open("tests/integration/test_integration_graph.py", "w") as f:
    f.write(content)


with open("tests/unit/test_git_consolidation.py", "r") as f:
    content = f.read()

content = content.replace("ALL_PUBLIC_METHODS =", "ALL_PUBLIC_METHODS: typing.ClassVar[list[str]] =")
content = content.replace("import pytest", "import pytest\nimport typing")


with open("tests/unit/test_git_consolidation.py", "w") as f:
    f.write(content)
