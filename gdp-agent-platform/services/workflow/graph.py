"""Workflow graph definition: states, transitions, and derived (implicit) transitions.

The JSON seed file is the single source of truth. The deploy script writes the expanded
graph into CORE.WORKFLOW_STATE / CORE.WORKFLOW_TRANSITION; stored procedures read it back
from those tables and evaluate it with services.workflow.state_machine.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ACTIVE_PHASE = 1

KINDS = {"PENDING", "RUNNING", "REVIEW", "DONE", "BLOCKED", "TERMINAL", "FAILED"}
ACTORS = {"SYSTEM", "HUMAN", "ANY"}

FAILED_STATE = "FAILED"
CANCELLED_STATE = "CANCELLED"
INITIAL_STATE = "CREATED"

GUARD_RETRY = "RETRY_FROM_FAILED_STATE"


@dataclass(frozen=True)
class State:
    state: str
    stage: Optional[str]
    kind: str
    ordinal: int
    phase: int
    enabled: bool
    retry_to: str


@dataclass(frozen=True)
class Transition:
    from_state: str
    to_state: str
    actor: str
    guard: Optional[str]
    enabled: bool


@dataclass(frozen=True)
class WorkflowGraph:
    version: str
    states: Dict[str, State]
    transitions: Tuple[Transition, ...]

    def outgoing(self, state: str) -> List[Transition]:
        return [t for t in self.transitions if t.from_state == state]

    def find(self, from_state: str, to_state: str) -> Optional[Transition]:
        for t in self.transitions:
            if t.from_state == from_state and t.to_state == to_state:
                return t
        return None


def _is_terminal(kind: str) -> bool:
    return kind == "TERMINAL"


def build_graph(raw: dict, active_phase: int = ACTIVE_PHASE) -> WorkflowGraph:
    """Validate the raw definition and expand implicit FAILED / retry / CANCELLED transitions."""
    states: Dict[str, State] = {}
    for s in raw["states"]:
        name = s["state"]
        assert name not in states, f"duplicate state {name}"
        assert s["kind"] in KINDS, f"{name}: unknown kind {s['kind']}"
        states[name] = State(
            state=name,
            stage=s.get("stage"),
            kind=s["kind"],
            ordinal=int(s["ordinal"]),
            phase=int(s["phase"]),
            enabled=int(s["phase"]) <= active_phase,
            retry_to=s.get("retry_to", name),
        )

    for name in (INITIAL_STATE, FAILED_STATE, CANCELLED_STATE):
        assert name in states, f"required state {name} missing"
    for s in states.values():
        assert s.retry_to in states, f"{s.state}: retry_to {s.retry_to} is not a state"

    explicit: List[Transition] = []
    seen = set()
    for t in raw["transitions"]:
        f, to, actor = t["from"], t["to"], t["actor"]
        assert f in states and to in states, f"transition {f}->{to} references unknown state"
        assert actor in ACTORS, f"transition {f}->{to}: unknown actor {actor}"
        assert (f, to) not in seen, f"duplicate transition {f}->{to}"
        assert f not in (FAILED_STATE, CANCELLED_STATE), f"explicit transition out of {f} is not allowed"
        seen.add((f, to))
        explicit.append(Transition(f, to, actor, t.get("guard"),
                                   states[f].enabled and states[to].enabled))

    implicit: List[Transition] = []
    for s in states.values():
        if s.state in (FAILED_STATE, CANCELLED_STATE) or _is_terminal(s.kind):
            continue
        # Any active state can fail or be cancelled.
        implicit.append(Transition(s.state, FAILED_STATE, "SYSTEM", None, s.enabled))
        implicit.append(Transition(s.state, CANCELLED_STATE, "ANY", None, s.enabled))
    for target in sorted({s.retry_to for s in states.values()
                          if s.state not in (FAILED_STATE, CANCELLED_STATE) and not _is_terminal(s.kind)}):
        implicit.append(Transition(FAILED_STATE, target, "ANY", GUARD_RETRY, states[target].enabled))
    implicit.append(Transition(FAILED_STATE, CANCELLED_STATE, "ANY", None, True))

    for t in implicit:
        assert (t.from_state, t.to_state) not in seen, \
            f"explicit transition {t.from_state}->{t.to_state} duplicates an implicit one"
        seen.add((t.from_state, t.to_state))

    return WorkflowGraph(raw["graph_version"], states, tuple(explicit + implicit))


def load_graph(path: Path, active_phase: int = ACTIVE_PHASE) -> WorkflowGraph:
    return build_graph(json.loads(Path(path).read_text(encoding="utf-8")), active_phase)


def graph_from_rows(version: str, state_rows: Iterable[dict], transition_rows: Iterable[dict]) -> WorkflowGraph:
    """Rebuild a graph from the CORE.WORKFLOW_STATE / CORE.WORKFLOW_TRANSITION rows (already expanded)."""
    states = {
        r["STATE"]: State(r["STATE"], r["STAGE"], r["KIND"], int(r["ORDINAL"]), int(r["PHASE"]),
                          bool(r["ENABLED"]), r["RETRY_TO"])
        for r in state_rows
    }
    transitions = tuple(
        Transition(r["FROM_STATE"], r["TO_STATE"], r["ACTOR"], r["GUARD"], bool(r["ENABLED"]))
        for r in transition_rows
    )
    return WorkflowGraph(version, states, transitions)
