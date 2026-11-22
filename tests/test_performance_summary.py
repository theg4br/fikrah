"""Offline latency summaries stay bounded and distinguish failed operations."""

import json

from scripts.summarize_performance import SAMPLE_LIMIT, percentile, summarize


def event(duration, **kwargs):
    return json.dumps(
        {
            "event": "performance",
            "stage": "search.total",
            "outcome": "ok",
            "duration_ms": duration,
            **kwargs,
        }
    )


def test_summary_ignores_other_logs_and_groups_status_and_route():
    groups, dropped = summarize(
        [
            "worker ready",
            "{broken json",
            "null",
            "[]",
            "{}",
            event(10),
            "worker-1 | " + event(30, dropped_records=2),
            event(90, outcome="error"),
            event(5, route="/health", method="GET"),
            event(-1),
            event(float("nan")),
            event(True),
            event(3, stage=[]),
        ]
    )
    success = groups[("search.total", "-", "-", "ok")]
    assert success.count == 2
    assert success.maximum == 30
    assert len(groups) == 3
    assert dropped == 2
    assert percentile(sorted(success.durations), 95) == 30


def test_summary_keeps_a_bounded_sample_and_full_count():
    groups, _ = summarize(event(i) for i in range(SAMPLE_LIMIT + 3))
    sample = next(iter(groups.values()))
    assert sample.count == SAMPLE_LIMIT + 3
    assert len(sample.durations) == SAMPLE_LIMIT
    assert sample.durations[0] == 3
    assert sample.maximum == SAMPLE_LIMIT + 2


def test_summary_limits_distinct_groups():
    groups, _ = summarize(event(1, stage=str(i)) for i in range(1100))
    assert len(groups) == 1024
