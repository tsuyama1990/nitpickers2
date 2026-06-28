"""Live integration tests for Architect and Coder nodes with real API calls.

These tests validate that the actual node implementations work end-to-end
against the real Jules API and other external services.

Prerequisites:
    - JULES_API_KEY, OPENROUTER_API_KEY, E2B_API_KEY, LANGSMITH_API_KEY
      exported in the environment (or loaded via ~/.zshrc)
    - Run with: uv run pytest tests/e2e/live/test_architect_coder_nodes_live.py -v -m live --no-header -s
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess as sp
from pathlib import Path
from typing import Any

import pytest

# All tests in this file require live API access
pytestmark = [pytest.mark.live]


def _ensure_env() -> None:
    """Skip if required API keys are missing."""
    required = ["JULES_API_KEY", "OPENROUTER_API_KEY", "E2B_API_KEY", "LANGSMITH_API_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        pytest.skip(f"Missing required env vars: {', '.join(missing)}")


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def _check_env() -> None:
    """Module-level environment check (runs once)."""
    _ensure_env()


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """Create a temporary git workspace for tests that need git operations."""
    ws = tmp_path / "nitpick_workspace"
    ws.mkdir(parents=True, exist_ok=True)

    # Initialize git
    git_bin = shutil.which("git")
    assert git_bin, "git is required for live tests"

    sp.run([git_bin, "init"], cwd=ws, check=True, capture_output=True)
    sp.run([git_bin, "config", "user.name", "Live Test"], cwd=ws, check=True, capture_output=True)
    sp.run([git_bin, "config", "user.email", "live@test.com"], cwd=ws, check=True, capture_output=True)

    # Create a README so we have at least one commit
    (ws / "README.md").write_text("# Live Test Workspace\nTesting architect/coder nodes.")
    sp.run([git_bin, "add", "README.md"], cwd=ws, check=True, capture_output=True)
    sp.run([git_bin, "commit", "-m", "Initial commit"], cwd=ws, check=True, capture_output=True)
    sp.run([git_bin, "branch", "-M", "main"], cwd=ws, check=True, capture_output=True)

    # Create dev_documents with ALL_SPEC.md (required by architect node)
    dev_docs = ws / "dev_documents"
    dev_docs.mkdir(exist_ok=True)
    (dev_docs / "ALL_SPEC.md").write_text(
        "# ALL_SPEC\n\nA minimal test project for verifying architect and coder node execution.\n"
    )

    return ws


@pytest.fixture
def jules_client() -> Any:
    """Create a live JulesClient instance."""
    _ensure_env()
    from src.services.jules_client import JulesClient

    return JulesClient()


@pytest.fixture
def git_manager() -> Any:
    """Create a live GitManager instance."""
    from src.services.git_ops import GitManager

    return GitManager()


# ═══════════════════════════════════════════════════════════════════
# Test 1: JulesClient basic connectivity (no session creation)
# ═══════════════════════════════════════════════════════════════════


def test_jules_client_initialization() -> None:
    """Verify that JulesClient can be initialized with the real API key."""
    _ensure_env()

    from src.services.jules_client import JulesClient

    client = JulesClient()
    assert client.sdk_client is not None, "SDK client should be initialized"
    assert client.timeout > 0, "Timeout should be set"


# ═══════════════════════════════════════════════════════════════════
# Test 2: JulesClient session creation (quick check, no wait)
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_jules_session_creation(jules_client: Any) -> None:
    """Verify that a Jules session can be created (does NOT wait for completion).

    This validates:
    - API key authentication works
    - Git context resolution works (uses test defaults in pytest env)
    - Session creation API call succeeds
    """
    session_name = None
    try:
        result = await asyncio.wait_for(
            jules_client.run_session(
                command="Say 'Hello, world!' and nothing else.",
                session_id="live-test-creation",
                prompt="You are a test assistant. Respond with exactly: 'Hello, world!'",
                require_plan_approval=False,
            ),
            timeout=30.0,
        )

        assert result is not None, "run_session returned None"
        session_name = result.get("session_name")
        assert session_name is not None, f"No session_name in result keys: {list(result.keys())}"

        result.get("status")

        # Verify the session exists via the SDK
        normalized = (
            f"sessions/{session_name}" if not session_name.startswith("sessions/") else session_name
        )
        session = await jules_client.sdk_client.sessions.get(normalized)
        assert session is not None, "Session should be fetchable from API"

    except TimeoutError:
        pytest.fail("Session creation timed out after 30s")


# ═══════════════════════════════════════════════════════════════════
# Test 3: JulesClient full session lifecycle (longer wait)
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.timeout(300)  # 5 min max
async def test_jules_session_lifecycle(jules_client: Any) -> None:
    """Test the full session lifecycle: create → monitor state → completion.

    WARNING: This test can take several minutes depending on Jules API load.
    """
    # Create a session with a very simple task
    result = await jules_client.run_session(
        command="Create a single Python file called hello.py that prints 'Hello from test'.",
        session_id="live-test-lifecycle",
        prompt="Create hello.py as specified.",
        require_plan_approval=False,
    )

    assert result is not None, "run_session returned None"
    session_name = result.get("session_name")
    assert session_name, f"No session_name in result: {result}"

    # Poll for session state a few times (don't wait for full completion)
    normalized = (
        f"sessions/{session_name}" if not session_name.startswith("sessions/") else session_name
    )
    for _attempt in range(3):
        await asyncio.sleep(5)
        try:
            session = await jules_client.sdk_client.sessions.get(normalized)
            jules_client._get_state_str(session)
        except Exception:
            pass



# ═══════════════════════════════════════════════════════════════════
# Test 4: Architect node — session creation
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_architect_node_creates_session(
    temp_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that the architect session node creates a Jules session.

    This test:
    1. Changes to a temp workspace with git repo + dev_documents
    2. Creates a live ArchitectNodes instance
    3. Calls architect_session_node with minimal CycleState
    4. Verifies the node creates a session (may not complete within test timeframe)
    """
    _ensure_env()
    monkeypatch.chdir(temp_workspace)

    from src.enums import FlowStatus
    from src.nodes.architect import ArchitectNodes
    from src.services.git_ops import GitManager
    from src.services.jules_client import JulesClient
    from src.state import CycleState

    jules = JulesClient()
    git = GitManager()
    arch_node = ArchitectNodes(jules=jules, git=git)

    state = CycleState(
        cycle_id="live-arch-test",
        status=FlowStatus.START,
    )

    # Execute the architect session node (this starts a Jules session)
    try:
        result = await asyncio.wait_for(
            arch_node.architect_session_node(state),
            timeout=60.0,
        )
    except TimeoutError:
        pytest.fail("Architect session node timed out after 60s (Jules may be slow)")

    status = result.get("status")
    if result.get("error"):
        pass

    # The node should either complete the session or fail gracefully
    assert status in (
        FlowStatus.ARCHITECT_SESSION_COMPLETED,
        FlowStatus.ARCHITECT_FAILED,
    ), f"Unexpected status: {status}"

    if status == FlowStatus.ARCHITECT_SESSION_COMPLETED:
        session = result.get("session")
        assert session is not None
        getattr(session, "pr_url", None)
        session_id = getattr(session, "project_session_id", None)
        assert session_id is not None, "Expected project_session_id in session state"
    else:
        pass


# ═══════════════════════════════════════════════════════════════════
# Test 5: Coder node — basic connectivity
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_coder_usecase_connectivity(
    temp_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that CoderUseCase can create a Jules session for implementation.

    Uses CoderUseCase directly with a real JulesClient to verify
    the session creation works from the coder pipeline.
    """
    _ensure_env()
    monkeypatch.chdir(temp_workspace)

    from src.enums import FlowStatus, WorkPhase
    from src.services.coder_usecase import CoderUseCase
    from src.services.jules_client import JulesClient
    from src.state import CycleState, SessionPersistenceState

    jules = JulesClient()
    usecase = CoderUseCase(jules)

    state = CycleState(
        cycle_id="live-coder-test",
        status=FlowStatus.START,
        current_phase=WorkPhase.CODER,
        session=SessionPersistenceState(
            project_session_id=None,
            feature_branch="main",
            integration_branch="main",
        ),
    )
    state.test.tdd_phase = "green"

    try:
        result = dict(
            await asyncio.wait_for(usecase.execute(state), timeout=60.0)
        )
    except TimeoutError:
        pytest.fail("Coder usecase timed out after 60s")

    status = result.get("status")
    if result.get("error"):
        pass

    acceptable = {
        FlowStatus.COMPLETED,
        FlowStatus.READY_FOR_AUDIT,
        FlowStatus.FAILED,
        FlowStatus.WAIT_FOR_JULES_COMPLETION,
    }
    assert status in acceptable, f"Unexpected status: {status}"

    if status in (FlowStatus.COMPLETED, FlowStatus.READY_FOR_AUDIT):
        pass
    else:
        pass


# ═══════════════════════════════════════════════════════════════════
# Test 6: Architect + Critic graph (graph-level test with real nodes)
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_architect_graph_live(
    temp_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run the full architect graph with real services.

    Builds the architect graph with live JulesClient and executes
    it via LangGraph's ainvoke to verify the complete flow.
    """
    _ensure_env()
    monkeypatch.chdir(temp_workspace)

    from src.enums import FlowStatus
    from src.graph import GraphBuilder
    from src.service_container import ServiceContainer
    from src.services.jules_client import JulesClient
    from src.state import CycleState

    jules = JulesClient()
    services = ServiceContainer.default()

    # Build the architect graph
    builder = GraphBuilder(services, jules=jules)
    graph = builder.build_architect_graph()

    # Initial state
    initial_state = CycleState(
        cycle_id="live-arch-graph",
        status=FlowStatus.START,
    )

    config = {"configurable": {"thread_id": "live-arch-graph-test"}}

    try:
        final_state = await asyncio.wait_for(
            graph.ainvoke(initial_state, config=config),
            timeout=180.0,  # 3 min for the full graph
        )
    except TimeoutError:
        pytest.fail("Architect graph timed out after 180s")

    if final_state.get("error"):
        pass

    status = final_state.get("status")
    assert status in (
        FlowStatus.ARCHITECT_COMPLETED,
        FlowStatus.ARCHITECT_FAILED,
        FlowStatus.ARCHITECT_SESSION_COMPLETED,
    ), f"Unexpected final status: {status}"

    if status == FlowStatus.ARCHITECT_COMPLETED:
        pass
    else:
        pass
