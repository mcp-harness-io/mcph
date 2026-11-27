from mcph.rules import Finding, Rule, RuleInfo, Severity, run_rules


def test_run_rules_attaches_the_rule_id_and_severity_to_each_message(snapshot_with):
    no_delete = Rule(
        id="acme.no_delete",
        description="No delete tools.",
        severity=Severity.ERROR,
        check=lambda snapshot: [
            f"{tool.name} deletes data"
            for tool in snapshot.tools
            if tool.name.startswith("delete")
        ],
    )

    findings = run_rules(snapshot_with("get_x", "delete_x", "delete_y"), [no_delete])

    assert findings == [
        Finding("acme.no_delete", Severity.ERROR, "delete_x deletes data"),
        Finding("acme.no_delete", Severity.ERROR, "delete_y deletes data"),
    ]


def test_run_rules_runs_every_rule_in_order(snapshot_with):
    first = Rule("a.first", "", Severity.WARN, lambda snapshot: ["one"])
    second = Rule("a.second", "", Severity.ERROR, lambda snapshot: ["two"])

    findings = run_rules(snapshot_with("x"), [first, second])

    assert [(f.rule_id, f.message) for f in findings] == [("a.first", "one"), ("a.second", "two")]


def test_no_rules_and_quiet_rules_give_no_findings(snapshot_with):
    quiet = Rule("a.quiet", "", Severity.WARN, lambda snapshot: [])

    assert run_rules(snapshot_with("x"), []) == []
    assert run_rules(snapshot_with("x"), [quiet]) == []


def test_a_rule_is_what_it_is_called_plus_how_to_check_it():
    rule = Rule("a.rule", "Does a thing.", Severity.WARN, lambda snapshot: [])

    assert isinstance(rule, RuleInfo)
    assert (rule.id, rule.description, rule.severity) == ("a.rule", "Does a thing.", Severity.WARN)
