"""Reject CI runs that silently skip required database and worker integration tests."""

import argparse
import sys
from pathlib import Path
from xml.etree import ElementTree

REQUIRED_SUITES = {
    "tests.test_postgres_concurrency",
    "tests.test_project_ownership",
    "tests.test_migration_environment",
    "tests.test_worker_deadlines",
    "tests.test_worker_queue_isolation",
}


def check_required_suites(report: Path) -> dict[str, int]:
    counts = dict.fromkeys(REQUIRED_SUITES, 0)
    for case in ElementTree.parse(report).iter("testcase"):
        suite = case.get("classname", "")
        if suite not in counts:
            continue
        if any(case.find(tag) is not None for tag in ("skipped", "failure", "error")):
            raise ValueError(f"Required integration test did not pass: {suite}.{case.get('name')}")
        counts[suite] += 1
    missing = sorted(suite for suite, count in counts.items() if count == 0)
    if missing:
        raise ValueError(f"Required integration suites were not collected: {', '.join(missing)}")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        counts = check_required_suites(args.report)
    except (OSError, ElementTree.ParseError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"Required integration suites passed: {sum(counts.values())} tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
