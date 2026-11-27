import pytest

from mcph.rules import Finding, Rule, RuleInfo, Severity, apply_rule_settings, enabled_rules

FINDINGS = [
    Finding("a.one", Severity.WARN, "first"),
    Finding("a.two", Severity.ERROR, "second"),
    Finding("a.one", Severity.WARN, "third"),
]


def applied(settings: dict[str, str], findings=FINDINGS) -> list[Finding]:
    return list(apply_rule_settings(findings, settings))


def test_rules_that_are_not_configured_keep_their_findings():
    assert applied({}) == FINDINGS
    assert applied({"other.rule": "off"}) == FINDINGS


def test_a_rule_that_is_off_loses_all_its_findings():
    assert applied({"a.one": "off"}) == [FINDINGS[1]]
    assert applied({"a.one": "off", "a.two": "off"}) == []


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
    [finding] = applied({"a.rule": setting}, [Finding("a.rule", default, "problem")])

    assert finding == Finding("a.rule", expected, "problem")


def test_only_the_configured_rules_change_and_the_order_is_kept():
    result = applied({"a.one": "error"})

    assert result == [
        Finding("a.one", Severity.ERROR, "first"),
        Finding("a.two", Severity.ERROR, "second"),
        Finding("a.one", Severity.ERROR, "third"),
    ]


def test_findings_can_be_a_one_shot_iterator():
    assert applied({"a.one": "off"}, iter(FINDINGS)) == [FINDINGS[1]]


def test_enabled_rules_leaves_out_the_rules_that_are_off_and_keeps_the_order():
    rules = [Rule(f"a.{name}", "", Severity.WARN, lambda snapshot: []) for name in "abc"]

    assert [rule.id for rule in enabled_rules(rules, {})] == ["a.a", "a.b", "a.c"]
    assert [rule.id for rule in enabled_rules(rules, {"a.b": "off"})] == ["a.a", "a.c"]
    assert [rule.id for rule in enabled_rules(rules, {"a.a": "error", "a.c": "warn"})] == [
        "a.a",
        "a.b",
        "a.c",
    ]
    assert enabled_rules(rules, {"a.a": "off", "a.b": "off", "a.c": "off"}) == []


def test_enabled_rules_works_for_rule_descriptions_too():
    infos = [RuleInfo("a.x", "X.", Severity.WARN), RuleInfo("a.y", "Y.", Severity.ERROR)]

    assert enabled_rules(infos, {"a.x": "off"}) == [infos[1]]
