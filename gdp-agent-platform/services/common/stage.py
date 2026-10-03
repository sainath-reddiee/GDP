"""Moves a run through a stage's states via the workflow state machine."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Sequence

from services.common.sql import clip
from services.workflow.procedures import _apply_transition, _get_run, _load_graph, _state_payload


class Stage:
    def __init__(self, session, run_id: str):
        self.session = session
        self.run_id = run_id
        self.graph = _load_graph(session)
        self.run = _get_run(session, run_id)

    @property
    def state(self) -> str:
        return self.run["CURRENT_STATE"]

    def require(self, *states: str) -> None:
        if self.state not in states:
            raise ValueError(f"TRANSITION_REJECTED: run is in {self.state}; this step needs {' or '.join(states)}")

    def move(self, to_state: str, reason: str, details: Optional[Dict[str, Any]] = None,
             in_transaction: Optional[Callable[[str], None]] = None) -> None:
        _apply_transition(self.session, self.graph, self.run, to_state, "SYSTEM", clip(reason), details or {},
                          in_transaction=in_transaction)
        self.run = _get_run(self.session, self.run_id)

    def walk(self, path: Sequence[str], reason: str) -> None:
        """Advance through consecutive states, starting wherever the run currently is on the path."""
        start = path.index(self.state) + 1 if self.state in path else 0
        for target in path[start:]:
            self.move(target, reason)

    def fail(self, exc: Exception, details: Optional[Dict[str, Any]] = None) -> None:
        self.run = _get_run(self.session, self.run_id)
        self.move("FAILED", f"{type(exc).__name__}: {exc}", details)

    def payload(self) -> Dict[str, Any]:
        return _state_payload(self.graph, _get_run(self.session, self.run_id))
