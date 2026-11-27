from mcph.extensions import load_pack, resolve_pack
from mcph.rules import Severity


def test_the_core_pack_ships_with_mcph_under_the_name_core(core_pack):
    assert core_pack.manifest.name == "core"
    assert core_pack.source == "builtin:core"
    assert core_pack.manifest.description


def test_these_are_the_rules_of_the_core_pack(core_pack):
    # Rule ids are part of mcph.yaml: renaming one breaks configs, so it is a deliberate change.
    assert [rule.id for rule in core_pack.rules] == [
        "core.tool_name_snake_case",
        "oauth.metadata",
        "oauth.issuer",
        "oauth.pkce",
        "oauth.registration",
    ]


def test_every_rule_has_a_description_a_default_severity_and_a_namespace(core_pack):
    for rule in core_pack.rules:
        assert rule.description.strip(), rule.id
        assert isinstance(rule.severity, Severity), rule.id
        assert rule.id.split(".")[0] in {"core", "oauth"}, rule.id


def test_the_default_severities(core_rules):
    assert {rule_id: rule.severity.value for rule_id, rule in core_rules.items()} == {
        "core.tool_name_snake_case": "warn",
        "oauth.metadata": "error",
        "oauth.issuer": "error",
        "oauth.pkce": "error",
        "oauth.registration": "warn",
    }


def test_the_oauth_checks_are_about_http_servers_and_do_not_need_a_connection(core_rules):
    oauth = [rule for rule_id, rule in core_rules.items() if rule_id.startswith("oauth.")]

    assert len(oauth) == 4
    assert [rule.transports for rule in oauth] == [("http",)] * 4
    assert not any(rule.needs_connection for rule in oauth)


def test_the_snake_case_rule_is_about_every_server_and_needs_the_connection(core_rules):
    rule = core_rules["core.tool_name_snake_case"]

    assert rule.transports == ("stdio", "http")
    assert rule.needs_connection


def test_the_core_pack_can_be_loaded_again_by_another_run(core_pack, tmp_path):
    again = load_pack(resolve_pack("builtin:core", tmp_path))

    assert [rule.id for rule in again.rules] == [rule.id for rule in core_pack.rules]
