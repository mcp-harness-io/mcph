from mcph.rules import Severity
from mcph.rules.builtin import BUILTIN_RULES, OAUTH_RULES, known_rules
from mcph.rules.builtin.tool_name_snake_case import TOOL_NAME_SNAKE_CASE


def test_these_are_the_rules_that_can_be_configured():
    # Rule ids are part of mcph.yaml: renaming one breaks configs, so it is a deliberate change.
    assert list(known_rules()) == [
        "core.tool_name_snake_case",
        "oauth.metadata",
        "oauth.issuer",
        "oauth.pkce",
        "oauth.registration",
    ]


def test_the_builtin_rules_are_the_core_rule_followed_by_the_oauth_checks():
    assert BUILTIN_RULES == (TOOL_NAME_SNAKE_CASE, *OAUTH_RULES)
    assert len(known_rules()) == len(BUILTIN_RULES)  # no id is used twice


def test_every_rule_has_a_description_a_default_severity_and_a_namespace():
    for rule_id, rule in known_rules().items():
        assert rule.id == rule_id
        assert rule.description.strip(), rule_id
        assert isinstance(rule.severity, Severity), rule_id
        assert rule_id.split(".")[0] in {"core", "oauth"}, rule_id


def test_the_oauth_checks_are_about_http_servers_and_do_not_need_a_connection():
    assert [rule.transports for rule in OAUTH_RULES] == [("http",)] * 4
    assert not any(rule.needs_connection for rule in OAUTH_RULES)


def test_the_snake_case_rule_is_about_every_server_and_needs_the_connection():
    assert TOOL_NAME_SNAKE_CASE.transports == ("stdio", "http")
    assert TOOL_NAME_SNAKE_CASE.needs_connection
