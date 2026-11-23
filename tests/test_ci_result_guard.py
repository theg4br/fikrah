"""CI must fail when required integration coverage is skipped or absent."""

from xml.etree import ElementTree

import pytest

from scripts.check_ci_results import REQUIRED_SUITES, check_required_suites


def write_report(tmp_path, *, omitted=None, outcome=None):
    root = ElementTree.Element("testsuite")
    for suite in sorted(REQUIRED_SUITES - ({omitted} if omitted else set())):
        case = ElementTree.SubElement(root, "testcase", classname=suite, name="test_real_service")
        if outcome:
            ElementTree.SubElement(case, outcome)
    path = tmp_path / "results.xml"
    ElementTree.ElementTree(root).write(path)
    return path


def test_ci_accepts_present_successful_integration_suites(tmp_path):
    counts = check_required_suites(write_report(tmp_path))
    assert counts == dict.fromkeys(REQUIRED_SUITES, 1)


@pytest.mark.parametrize("outcome", ["skipped", "failure", "error"])
def test_ci_rejects_skipped_or_failed_required_tests(tmp_path, outcome):
    with pytest.raises(ValueError, match="did not pass"):
        check_required_suites(write_report(tmp_path, outcome=outcome))


def test_ci_rejects_missing_integration_suites(tmp_path):
    with pytest.raises(ValueError, match="not collected"):
        check_required_suites(write_report(tmp_path, omitted="tests.test_worker_deadlines"))
