import pytest

from mcph.rules import Finding, Outcome, RuleResult, Severity, apply_rule_settings, is_off

WARNING = Finding("a.rule", Severity.WARN, "first")
ERROR = Finding("a.rule", Severity.ERROR, "second")


def test_only_off_turns_a_rule_off():
    settings = {"a.off": "off", "a.warn": "warn", "a.error": "error"}

    assert is_off("a.off", settings)
    assert not is_off("a.warn", settings)
    assert not is_off("a.error", settings)
    assert not is_off("a.unlisted", settings)
    assert not is_off("a.off", {})


def test_a_rule_that_is_not_configured_keeps_its_result():
    result = RuleResult("a.rule", findings=(WARNING, ERROR))

    assert apply_rule_settings(result, {}) is result
    assert apply_rule_settings(result, {"other.rule": "off"}) is result


def test_skipped_rules_that_are_not_configured_stay_skipped():
    result = RuleResult.skipped("a.rule", "no reason")

    assert apply_rule_settings(result, {"a.other": "error"}) is result


@pytest.mark.parametrize(
    "result",
    [
        RuleResult("a.rule"),
        RuleResult("a.rule", findings=(WARNING,)),
        RuleResult.skipped("a.rule", "because"),
    ],
)
def test_a_rule_that_is_off_becomes_an_off_result_without_findings(result):
    applied = apply_rule_settings(result, {"a.rule": "off"})

    assert applied == RuleResult("a.rule", Outcome.OFF)
    assert applied.findings == () and applied.reason == ""


@pytest.mark.parametrize(
    ("default", "setting", "expected"),
    [
        (Severity.WARN, "error", Severity.ERROR),
        (Severity.ERROR, "warn", Severity.WARN),
        (Severity.WARN, "warn", Severity.WARN),
        (Severity.ERROR, "error", Severity.ERROR),
    ],
)
def test_the_configured_severity_replaces_the_default(default, setting, expected):
    result = RuleResult("a.rule", findings=(Finding("a.rule", default, "problem"),))

    applied = apply_rule_settings(result, {"a.rule": setting})

    assert applied == RuleResult("a.rule", findings=(Finding("a.rule", expected, "problem"),))


def test_every_finding_of_the_rule_gets_the_severity_and_nothing_else_changes():
    result = RuleResult("a.rule", findings=(WARNING, ERROR))

    applied = apply_rule_settings(result, {"a.rule": "warn", "a.other": "off"})

    assert applied.outcome is Outcome.RAN
    assert [(f.rule_id, f.severity, f.message) for f in applied.findings] == [
        ("a.rule", Severity.WARN, "first"),
        ("a.rule", Severity.WARN, "second"),
    ]


def test_a_rule_that_passed_still_passed_with_a_severity_configured():
    assert apply_rule_settings(RuleResult("a.rule"), {"a.rule": "error"}) == RuleResult("a.rule")


def test_a_configured_severity_does_not_make_a_skipped_rule_run():
    skipped = RuleResult.skipped("a.rule", "because")

    assert apply_rule_settings(skipped, {"a.rule": "error"}) == skipped
