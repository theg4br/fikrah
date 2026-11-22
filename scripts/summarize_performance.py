#!/usr/bin/env python3
"""Summarize FIKRAH timing JSON from stdin without retaining request contents."""

import argparse
import json
import math
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, field

SAMPLE_LIMIT = 10_000


@dataclass
class Samples:
    count: int = 0
    maximum: float = 0
    durations: deque = field(default_factory=lambda: deque(maxlen=SAMPLE_LIMIT))

    def add(self, duration: float) -> None:
        self.count += 1
        self.maximum = max(self.maximum, duration)
        self.durations.append(duration)


def summarize(lines):
    """Accept JSON lines or Compose-prefixed lines; ignore unrelated log messages."""
    groups = defaultdict(Samples)
    dropped = 0
    for line in lines:
        try:
            record = json.loads(line[line.index("{") :])
            if record.get("event") != "performance":
                continue
            stage = record["stage"]
            outcome = record["outcome"]
            route = record.get("route", "-")
            method = record.get("method", "-")
            duration = record["duration_ms"]
            if (
                not all(
                    isinstance(value, str) for value in (stage, outcome, route, method)
                )
                or isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not math.isfinite(duration)
                or duration < 0
            ):
                continue
            key = (stage, method, route, outcome)
            if key not in groups and len(groups) >= 1024:
                continue
            groups[key].add(duration)
            lost = record.get("dropped_records", 0)
            if isinstance(lost, int) and lost >= 0:
                dropped = max(dropped, lost)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return groups, dropped


def percentile(sorted_values, percent):
    return sorted_values[max(0, math.ceil(len(sorted_values) * percent / 100) - 1)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    groups, dropped = summarize(sys.stdin)
    print(
        "stage\tmethod\troute\toutcome\tcount\tsamples\tp50_ms\tp95_ms\tp99_ms\tmax_ms"
    )
    for key, values in sorted(groups.items()):
        ordered = sorted(values.durations)
        quantiles = [percentile(ordered, percent) for percent in (50, 95, 99)]
        print(
            "\t".join(
                (
                    *key,
                    str(values.count),
                    str(len(ordered)),
                    *(f"{value:.3f}" for value in (*quantiles, values.maximum)),
                )
            )
        )
    print(
        f"# Quantiles: last {SAMPLE_LIMIT} events per group; counts/max: all accepted events."
    )
    print(
        f"# Largest observed per-process dropped-record counter: {dropped} (not a global sum)."
    )


if __name__ == "__main__":
    main()
