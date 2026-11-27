from mcph.rules import BUILTIN_RULES, OAUTH_RULES, Severity, known_rules


def test_these_are_the_rules_that_can_be_configured():
    # Rule ids are part of mcph.yaml: renaming one breaks configs, so it is a deliberate change.
    assert list(known_rules()) == [
        "core.tool_name_snake_case",
        "oauth.metadata",
        "oauth.issuer",
        "oauth.pkce",
        "oauth.registration",
    ]


def test_the_rules_that_run_on_a_snapshot_and_the_oauth_checks_are_all_known():
    known = known_rules()

    assert all(rule.id in known for rule in (*BUILTIN_RULES, *OAUTH_RULES))
    assert len(known) == len(BUILTIN_RULES) + len(OAUTH_RULES)  # no id is used twice


def test_every_rule_has_a_description_a_default_severity_and_a_namespace():
    for rule_id, rule in known_rules().items():
        assert rule.id == rule_id
        assert rule.description.strip(), rule_id
        assert isinstance(rule.severity, Severity), rule_id
        assert rule_id.split(".")[0] in {"core", "oauth"}, rule_id
