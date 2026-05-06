from src.enums import FlowStatus, WorkPhase


def test_work_phase_values() -> None:
    """Test that WorkPhase enum has correct values."""
    assert WorkPhase.INIT.value == "init"
    assert WorkPhase.ARCHITECT.value == "architect"
    assert WorkPhase.ARCHITECT_DONE.value == "architect_done"
    assert WorkPhase.CODER.value == "coder"
    assert WorkPhase.REFACTORING.value == "refactoring"
    assert WorkPhase.QA.value == "qa"


def test_flow_status_values() -> None:
    """Test that FlowStatus enum has some key correct values."""
    assert FlowStatus.START.value == "start"
    assert FlowStatus.FAILED.value == "failed"
    assert FlowStatus.COMPLETED.value == "completed"
    assert FlowStatus.END.value == "end"
    assert FlowStatus.APPROVED.value == "approved"
    assert FlowStatus.REJECTED.value == "rejected"


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
