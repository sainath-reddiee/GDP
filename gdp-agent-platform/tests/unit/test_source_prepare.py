import pytest

from services.source.prepare import INTERRUPTED, prepare_plan


@pytest.mark.parametrize("state, failed_from, steps", [
    ("SOURCE_REGISTERED", None, ["access", "landing"]),
    ("ACCESS_APPROVED", None, ["landing"]),
    ("LANDING_PENDING", None, ["landing"]),
    ("LANDING_COMPLETE", None, []),
    ("FAILED", "ACCESS_VALIDATION", ["retry:SOURCE_REGISTERED", "access", "landing"]),
    ("FAILED", "LANDING_RUNNING", ["retry:LANDING_PENDING", "landing"]),
])
def test_prepare_resumes_from_current_state(state, failed_from, steps):
    assert prepare_plan(state, failed_from) == steps


@pytest.mark.parametrize("state, failed_from", [("CREATED", None), ("PROFILING_PENDING", None), ("FAILED", "MAPPING_PENDING")])
def test_prepare_rejects_states_outside_source(state, failed_from):
    with pytest.raises(ValueError):
        prepare_plan(state, failed_from)


def test_a_landing_that_died_is_failed_and_landed_again_once_it_has_not_moved_for_a_while():
    assert prepare_plan("LANDING_RUNNING", None, 45) == [INTERRUPTED, "retry:LANDING_PENDING", "landing"]
    for idle in (None, 5):
        with pytest.raises(ValueError, match="still running"):
            prepare_plan("LANDING_RUNNING", None, idle)
