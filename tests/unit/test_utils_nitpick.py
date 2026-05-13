import os
from unittest.mock import MagicMock, patch

import pytest

from src.utils import get_command_prefix


@pytest.mark.parametrize("open_error", [FileNotFoundError, PermissionError])
@patch("pathlib.Path.exists")
@patch("pathlib.Path.open")
def test_get_command_prefix_fallback_errors(
    mock_open: MagicMock, mock_exists: MagicMock, open_error: Exception
) -> None:
    """
    Test that get_command_prefix correctly falls back to 'uv run manage.py'
    when reading /proc/self/cgroup raises various errors.
    """
    # Method 1 fails: .dockerenv does not exist
    mock_exists.return_value = False

    # Method 2 fails: reading /proc/self/cgroup raises Exception
    mock_open.side_effect = open_error

    # Method 3 fails: DOCKER_CONTAINER is not set
    with patch.dict(os.environ, {}, clear=True):
        assert get_command_prefix() == "uv run manage.py"
