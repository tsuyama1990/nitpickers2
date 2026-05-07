import pytest
from pydantic import ValidationError

from src.jules_session_state import JulesSessionState, SessionStatus, add_set


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (None, None, set()),
        (None, set(), set()),
        (set(), None, set()),
        (set(), set(), set()),
        (None, {"a", "b"}, {"a", "b"}),
        ({"a", "b"}, None, {"a", "b"}),
        (set(), {"a", "b"}, {"a", "b"}),
        ({"a", "b"}, set(), {"a", "b"}),
        ({"a"}, {"b"}, {"a", "b"}),
        ({"a", "b"}, {"b", "c"}, {"a", "b", "c"}),
        ({"a", "b"}, {"a", "b"}, {"a", "b"}),
    ],
)
def test_add_set(a: set[str] | None, b: set[str] | None, expected: set[str]) -> None:
    assert add_set(a, b) == expected


def test_jules_session_state_defaults() -> None:
    state = JulesSessionState()
    assert state.session_url == ""
    assert state.session_name == "unknown"
    assert state.status == SessionStatus.MONITORING
    assert state.jules_state is None
    assert state.previous_jules_state is None
    assert state.processed_activity_ids == set()
    assert state.processed_completion_ids == set()
    assert state.processed_inquiry_ids == set()
    assert state.last_activity_count == 0
    assert state.current_inquiry is None
    assert state.current_inquiry_id is None
    assert state.plan_rejection_count == 0
    assert state.max_plan_rejections == 2
    assert state.require_plan_approval is False
    assert state.pr_url is None
    assert state.fallback_elapsed_seconds == 0
    assert state.fallback_max_wait == 900
    assert state.processed_fallback_ids == set()
    assert state.start_time == 0.0
    assert state.timeout_seconds == 7200.0
    assert state.poll_interval == 120.0
    assert state.error is None
    assert state.raw_data is None
    assert state.has_recent_activity is False
    assert state.completion_validated is False
    assert state.last_jules_state_change_time == 0.0
    assert state.stale_nudge_count == 0


def test_jules_session_state_custom_initialization() -> None:
    state = JulesSessionState(
        session_url="http://example.com/session",
        session_name="test_session",
        status=SessionStatus.INQUIRY_DETECTED,
        jules_state="waiting_for_user",
        previous_jules_state="working",
        processed_activity_ids={"act_1"},
        processed_completion_ids={"comp_1"},
        processed_inquiry_ids={"inq_1"},
        last_activity_count=5,
        current_inquiry="What next?",
        current_inquiry_id="inq_1",
        plan_rejection_count=1,
        max_plan_rejections=5,
        require_plan_approval=True,
        pr_url="http://example.com/pr",
        fallback_elapsed_seconds=300,
        fallback_max_wait=1800,
        processed_fallback_ids={"fall_1"},
        start_time=1000.0,
        timeout_seconds=3600.0,
        poll_interval=60.0,
        error="Some error",
        raw_data={"key": "value"},
        has_recent_activity=True,
        completion_validated=True,
        last_jules_state_change_time=1500.0,
        stale_nudge_count=2,
    )
    assert state.session_url == "http://example.com/session"
    assert state.session_name == "test_session"
    assert state.status == SessionStatus.INQUIRY_DETECTED
    assert state.jules_state == "waiting_for_user"
    assert state.previous_jules_state == "working"
    assert state.processed_activity_ids == {"act_1"}
    assert state.processed_completion_ids == {"comp_1"}
    assert state.processed_inquiry_ids == {"inq_1"}
    assert state.last_activity_count == 5
    assert state.current_inquiry == "What next?"
    assert state.current_inquiry_id == "inq_1"
    assert state.plan_rejection_count == 1
    assert state.max_plan_rejections == 5
    assert state.require_plan_approval is True
    assert state.pr_url == "http://example.com/pr"
    assert state.fallback_elapsed_seconds == 300
    assert state.fallback_max_wait == 1800
    assert state.processed_fallback_ids == {"fall_1"}
    assert state.start_time == 1000.0
    assert state.timeout_seconds == 3600.0
    assert state.poll_interval == 60.0
    assert state.error == "Some error"
    assert state.raw_data == {"key": "value"}
    assert state.has_recent_activity is True
    assert state.completion_validated is True
    assert state.last_jules_state_change_time == 1500.0
    assert state.stale_nudge_count == 2


def test_jules_session_state_invalid_status() -> None:
    with pytest.raises(ValidationError):
        JulesSessionState(status="invalid_status")  # type: ignore


def test_jules_session_state_invalid_type() -> None:
    with pytest.raises(ValidationError):
        JulesSessionState(last_activity_count="not_an_int")  # type: ignore
