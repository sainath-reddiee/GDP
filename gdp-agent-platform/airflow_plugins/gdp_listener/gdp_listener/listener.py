"""Airflow listener hooks that push DAG run and task instance changes to the GDP platform.

Works with Airflow 2.7+ and 3.x. Hook implementations name only the arguments they use (pluggy passes a subset), and
on_task_instance_failed takes `error` only where the hook spec has it (Airflow 2.10 and later). Every hook swallows
its own errors, so the plugin can never fail or slow down a task.
"""

from __future__ import annotations

from airflow.listeners import hookimpl

from gdp_listener import core

try:
    from airflow import __version__ as _AIRFLOW_VERSION
except Exception:  # pragma: no cover
    _AIRFLOW_VERSION = "2.7.0"


def _version(text: str) -> tuple:
    parts = []
    for part in str(text).split("+")[0].split(".")[:2]:
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


_FAILED_HAS_ERROR = _version(_AIRFLOW_VERSION) >= (2, 10)


def _dag_run(dag_run, state: str) -> None:
    core.safe(lambda: core.emit(core.dag_run_payload(dag_run, state)))


def _task(task_instance, state: str, error=None) -> None:
    core.safe(lambda: core.emit(core.task_payload(task_instance, state, error)))


@hookimpl
def on_dag_run_running(dag_run, msg):
    _dag_run(dag_run, "running")


@hookimpl
def on_dag_run_success(dag_run, msg):
    _dag_run(dag_run, "success")


@hookimpl
def on_dag_run_failed(dag_run, msg):
    _dag_run(dag_run, "failed")


@hookimpl
def on_task_instance_running(previous_state, task_instance):
    _task(task_instance, "running")


@hookimpl
def on_task_instance_success(previous_state, task_instance):
    _task(task_instance, "success")


if _FAILED_HAS_ERROR:
    @hookimpl
    def on_task_instance_failed(previous_state, task_instance, error):
        _task(task_instance, "failed", error)
else:
    @hookimpl
    def on_task_instance_failed(previous_state, task_instance):
        _task(task_instance, "failed")
