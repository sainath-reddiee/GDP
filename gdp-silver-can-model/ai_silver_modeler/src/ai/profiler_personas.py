"""Lightweight AI persona heuristics for profiling insights."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd

from utils.csv_sanitizer import sanitize_text


@dataclass
class AgentInsight:
    agent: str
    role: str
    summary: str
    highlights: List[str]
    rules: List[str]


def _format_columns(columns: pd.Series, limit: int = 5) -> List[str]:
    if columns.empty:
        return []
    return [f"{row['Column']} ({row['Completeness']:.1f}% complete)" for _, row in columns.head(limit).iterrows()]


def run_profiler_personas(df: pd.DataFrame) -> List[AgentInsight]:
    """Generate heuristic-based insights for the profiler dashboard."""
    if df.empty:
        return []

    insights: List[AgentInsight] = []

    avg_completeness = df['Completeness'].mean()
    median_null = df['Null %'].median()
    dominant_types = df['Type'].value_counts().head(3)
    type_summary = ", ".join([f"{dtype}: {count}" for dtype, count in dominant_types.items()])

    profiler_summary = (
        f"Overall completeness is {avg_completeness:.1f}% with a median null rate of {median_null:.1f}%. "
        f"Most frequent data types: {type_summary or 'unknown'}."
    )
    profiler_highlights = []
    profiler_rules = [
        f"Maintain ≥{avg_completeness:.0f}% completeness for critical attributes.",
        "Document source system refresh cadence for transparency.",
    ]
    insights.append(AgentInsight(
        agent="Atlas",
        role="Lead Data Profiler",
        summary=sanitize_text(profiler_summary),
        highlights=profiler_highlights,
        rules=profiler_rules,
    ))

    low_completeness = df[df['Completeness'] < 85].sort_values('Completeness')
    spike_nulls = df[df['Null %'] > 20].sort_values('Null %', ascending=False)
    qa_highlights = _format_columns(low_completeness)
    qa_rules = []
    if not low_completeness.empty:
        qa_rules.append(
            f"Columns below 85% completeness ({len(low_completeness)}) require remediation plans."
        )
    if not spike_nulls.empty:
        qa_rules.append("Introduce null/placeholder monitoring for high-null attributes.")

    qa_summary = """
I examine attribute health, focusing on completeness drops and null spikes. Columns flagged here should
feed the AI Companion and downstream rule automation.
""".strip()

    insights.append(AgentInsight(
        agent="Nova",
        role="Data Quality Investigator",
        summary=sanitize_text(qa_summary),
        highlights=qa_highlights,
        rules=qa_rules or ["All monitored attributes currently meet null thresholds."],
    ))

    # Rules architect suggestions
    rules_highlights: List[str] = []
    candidate_rules: List[str] = []

    for _, row in spike_nulls.head(5).iterrows():
        rules_highlights.append(f"{row['Column']} ~{row['Null %']:.1f}% null")
        candidate_rules.append(
            f"Ensure {row['Column']} is captured upstream or defaulted before landing in the Bronze layer."
        )

    if not candidate_rules:
        candidate_rules.append("Configure freshness alerts for the top 5 business-critical tables.")

    architect_summary = "I translate findings into governance rules so remediation work can be tracked."
    insights.append(AgentInsight(
        agent="Quill",
        role="Rules Architect",
        summary=sanitize_text(architect_summary),
        highlights=rules_highlights,
        rules=candidate_rules,
    ))

    return insights
