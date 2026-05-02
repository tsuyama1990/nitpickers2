from src.enums import FlowStatus, WorkPhase


def test_work_phase_values() -> None:
    """Test that WorkPhase enum has correct values."""
    assert WorkPhase.INIT == "init"
    assert WorkPhase.ARCHITECT == "architect"
    assert WorkPhase.ARCHITECT_DONE == "architect_done"
    assert WorkPhase.CODER == "coder"
    assert WorkPhase.REFACTORING == "refactoring"
    assert WorkPhase.QA == "qa"


def test_flow_status_values() -> None:
    """Test that FlowStatus enum has some key correct values."""
    assert FlowStatus.START == "start"
    assert FlowStatus.FAILED == "failed"
    assert FlowStatus.COMPLETED == "completed"
    assert FlowStatus.END == "end"
    assert FlowStatus.APPROVED == "approved"
    assert FlowStatus.REJECTED == "rejected"


def test_enums_are_strings() -> None:
    """Test that the enums behave like strings (StrEnum)."""
    assert isinstance(WorkPhase.INIT, str)
    assert isinstance(FlowStatus.START, str)
    assert WorkPhase.INIT.upper() == "INIT"
    assert FlowStatus.START.startswith("s")


def test_work_phase_iteration() -> None:
    """Test that WorkPhase can be iterated and has expected count."""
    phases = list(WorkPhase)
    assert len(phases) == 6
    assert "init" in phases


def test_flow_status_iteration() -> None:
    """Test that FlowStatus can be iterated and has expected count."""
    statuses = list(FlowStatus)
    # Count based on src/enums.py:
    # Common: 4
    # Architect: 3
    # Coder/Session: 6
    # Auditor: 6
    # UAT & Refactor: 7
    # QA: 2
    # Total: 4 + 3 + 6 + 6 + 7 + 2 = 28
    assert len(statuses) == 28
    assert "approved" in statuses
