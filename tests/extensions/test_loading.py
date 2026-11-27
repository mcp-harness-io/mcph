import pytest

from mcph.config import Config, ConfigError, DoctorConfig, StdioServerConfig, load_config
from mcph.extensions import load_extensions, load_packs

CORE_IDS = [
    "core.tool_name_snake_case",
    "oauth.metadata",
    "oauth.issuer",
    "oauth.pkce",
    "oauth.registration",
]
ADD_A_PACK = (
    "add a pack to `extends` in mcph.yaml "
    "(`extends: [builtin:core]` has the rules that ship with mcph)"
)

SERVERS = "servers:\n  - {name: orders, transport: stdio, command: uv}\n"

RULE_FILE = """
from mcph.extensions import doctor_rule


@doctor_rule(id="acme.no_delete", description="No tool deletes data.")
def no_delete(context):
    return []
"""


def config_with(tmp_path, text: str) -> Config:
    path = tmp_path / "project" / "mcph.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(SERVERS + text)
    return load_config(path)


def error_for(config: Config) -> str:
    with pytest.raises(ConfigError) as excinfo:
        load_extensions(config)
    return str(excinfo.value)


def test_a_config_without_extends_loads_nothing(tmp_path):
    registry = load_extensions(config_with(tmp_path, ""))

    assert registry.packs == () and registry.rules == ()


def test_the_core_pack_is_loaded_by_name(tmp_path):
    registry = load_extensions(config_with(tmp_path, "extends:\n  - builtin:core\n"))

    assert [pack.manifest.name for pack in registry.packs] == ["core"]
    assert [rule.id for rule in registry.rules] == CORE_IDS


def test_a_relative_directory_is_taken_from_the_directory_of_the_config_file(
    tmp_path, monkeypatch
):
    pack = tmp_path / "project" / "packs" / "acme"
    (pack / "rules").mkdir(parents=True)
    (pack / "mcph-pack.yaml").write_text('name: acme\nversion: "1"\nrules:\n  - rules/a.py\n')
    (pack / "rules" / "a.py").write_text(RULE_FILE)
    config = config_with(tmp_path, "extends:\n  - builtin:core\n  - ./packs/acme\n")
    monkeypatch.chdir(tmp_path)  # not the directory of the config

    registry = load_extensions(config)

    assert [pack.manifest.name for pack in registry.packs] == ["core", "acme"]
    assert [rule.id for rule in registry.rules] == [*CORE_IDS, "acme.no_delete"]


def test_settings_may_name_the_rules_of_any_loaded_pack(tmp_path):
    config = config_with(
        tmp_path,
        "extends:\n  - builtin:core\ndoctor:\n  rules:\n    oauth.pkce: warn\n"
        "    core.tool_name_snake_case: off\n",
    )

    assert [rule.id for rule in load_extensions(config).rules] == CORE_IDS


def test_a_pack_that_cannot_be_loaded_is_a_config_error_with_the_reason(tmp_path):
    config = config_with(tmp_path, "extends:\n  - ./packs/missing\n")

    message = error_for(config)

    assert message == (
        f"pack directory not found: {tmp_path / 'project' / 'packs' / 'missing'} "
        "(from './packs/missing')"
    )


def test_an_unknown_builtin_pack_lists_the_bundled_ones(tmp_path):
    message = error_for(config_with(tmp_path, "extends:\n  - builtin:nope\n"))

    assert message == (
        "no built-in pack named 'nope' in 'builtin:nope' (available: core)"
    )


def test_a_source_of_another_kind_is_unsupported(tmp_path):
    message = error_for(config_with(tmp_path, "extends:\n  - git+https://example.com/p.git\n"))

    assert message.startswith("unsupported pack source 'git+https://example.com/p.git'")


def test_an_unknown_rule_is_reported_with_a_suggestion_and_the_known_rules(tmp_path):
    config = config_with(
        tmp_path,
        "extends:\n  - builtin:core\ndoctor:\n  rules:\n    core.tool_name_snakecase: warn\n",
    )

    assert error_for(config).splitlines() == [
        f"Invalid config {config.path}:",
        "  doctor.rules: unknown rule 'core.tool_name_snakecase' "
        "(did you mean 'core.tool_name_snake_case'?); known rules: "
        + ", ".join(CORE_IDS),
    ]


def test_an_unknown_rule_that_resembles_nothing_gets_no_suggestion(tmp_path):
    config = config_with(
        tmp_path, "extends:\n  - builtin:core\ndoctor:\n  rules:\n    zzz: warn\n"
    )

    message = error_for(config)

    assert "unknown rule 'zzz'; known rules: " in message
    assert "did you mean" not in message


def test_every_unknown_rule_is_named_at_once(tmp_path):
    config = config_with(
        tmp_path,
        "extends:\n  - builtin:core\ndoctor:\n  rules:\n    oauth.pkse: off\n    zzz: warn\n"
        "    oauth.pkce: error\n",
    )

    assert error_for(config).splitlines()[1] == (
        "  doctor.rules: unknown rules 'oauth.pkse' (did you mean 'oauth.pkce'?), 'zzz'; "
        "known rules: " + ", ".join(CORE_IDS)
    )


def test_a_rule_of_a_pack_that_is_not_loaded_is_unknown_and_the_message_says_how_to_load_it(
    tmp_path,
):
    config = config_with(tmp_path, "doctor:\n  rules:\n    core.tool_name_snake_case: off\n")

    assert error_for(config).splitlines() == [
        f"Invalid config {config.path}:",
        f"  doctor.rules: unknown rule 'core.tool_name_snake_case': no rule is loaded; {ADD_A_PACK}",
    ]


def test_several_unknown_rules_without_any_pack_use_the_plural(tmp_path):
    config = config_with(tmp_path, "doctor:\n  rules:\n    a.one: off\n    b.two: warn\n")

    assert error_for(config).splitlines()[1] == (
        f"  doctor.rules: unknown rules 'a.one', 'b.two': no rule is loaded; {ADD_A_PACK}"
    )


def test_a_config_built_in_code_has_no_file_to_name():
    config = Config(
        servers=[StdioServerConfig(name="a", transport="stdio", command="x")],
        doctor=DoctorConfig(rules={"core.tool_name_snake_case": "off"}),
    )

    assert error_for(config).startswith("Invalid config:\n  doctor.rules: unknown rule ")


def test_load_packs_does_not_look_at_the_settings(tmp_path):
    config = config_with(
        tmp_path, "extends:\n  - builtin:core\ndoctor:\n  rules:\n    zzz: warn\n"
    )

    registry = load_packs(config)  # load_extensions would reject the unknown rule

    assert [pack.manifest.name for pack in registry.packs] == ["core"]
    with pytest.raises(ConfigError, match="unknown rule 'zzz'"):
        load_extensions(config)


def test_load_packs_reports_a_pack_that_cannot_be_loaded_as_a_config_error(tmp_path):
    config = config_with(tmp_path, "extends:\n  - ./packs/missing\n")

    with pytest.raises(ConfigError, match="pack directory not found"):
        load_packs(config)
