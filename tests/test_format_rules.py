from pathlib import Path

from mcph.doctor import format_rules
from mcph.extensions import LoadedPack, PackManifest
from mcph.rules import Rule, Severity

NO_CONFIG = "Rules (no mcph.yaml in the current directory)"
NO_RULES = (
    "No rules are loaded: add a pack to `extends` in mcph.yaml "
    "(`extends: [builtin:core]` has the rules that ship with mcph)."
)


def rule(rule_id: str, description: str, severity: Severity) -> Rule:
    return Rule(rule_id, description, severity, lambda context: [])


def pack(name: str, source: str, *rules: Rule) -> LoadedPack:
    return LoadedPack(
        PackManifest(name=name, version="1"), source, Path("/packs") / name, tuple(rules)
    )


ACME = pack(
    "acme",
    "./packs/acme",
    rule("acme.short", "Short.", Severity.WARN),
    rule("acme.a_longer_id", "Longer.", Severity.ERROR),
    rule("acme.silent", "", Severity.WARN),
)
OTHER = pack("other", "builtin:other", rule("other.rule", "Other.", Severity.WARN))


def test_the_rules_are_listed_under_the_pack_they_come_from():
    assert format_rules([ACME], {}, config_path=Path("/p/mcph.yaml")) == "\n".join(
        [
            "Rules (settings from /p/mcph.yaml)",
            "",
            "acme (./packs/acme)",
            "  acme.short        warn   Short.",
            "  acme.a_longer_id  error  Longer.",
            "  acme.silent       warn",
        ]
    )


def test_every_pack_gets_a_group_and_the_columns_line_up_across_them():
    text = format_rules([ACME, OTHER], {}, config_path=None)

    assert text.splitlines() == [
        NO_CONFIG,
        "",
        "acme (./packs/acme)",
        "  acme.short        warn   Short.",
        "  acme.a_longer_id  error  Longer.",
        "  acme.silent       warn",
        "",
        "other (builtin:other)",
        "  other.rule        warn   Other.",
    ]


def test_a_changed_rule_shows_its_default_and_the_columns_stay_aligned():
    settings = {"acme.short": "error", "acme.a_longer_id": "off"}

    text = format_rules([ACME], settings, config_path=Path("/p/mcph.yaml"))

    assert text.splitlines()[3:] == [
        "  acme.short        error (default: warn)  Short.",
        "  acme.a_longer_id  off (default: error)   Longer.",
        "  acme.silent       warn",
    ]


def test_a_setting_equal_to_the_default_shows_no_default():
    settings = {"acme.short": "warn", "acme.a_longer_id": "error"}

    assert "default:" not in format_rules([ACME], settings, config_path=Path("/p/mcph.yaml"))


def test_settings_for_rules_that_are_not_listed_are_ignored():
    text = format_rules([OTHER], {"acme.short": "off"}, config_path=None)

    assert text.splitlines()[3:] == ["  other.rule  warn  Other."]


def test_the_packs_and_their_rules_are_listed_in_the_order_given():
    text = format_rules([OTHER, ACME], {}, config_path=None)

    groups = [line for line in text.splitlines()[2:] if line and not line.startswith(" ")]
    assert groups == [
        "other (builtin:other)",
        "acme (./packs/acme)",
    ]
    assert text.index("other.rule") < text.index("acme.short")


def test_a_pack_without_rules_says_so():
    text = format_rules([pack("empty", "./empty")], {}, config_path=Path("/p/mcph.yaml"))

    assert text.splitlines()[2:] == ["empty (./empty)", "  (no rules)", "", NO_RULES]


def test_without_any_pack_there_is_no_rule_and_the_listing_says_how_to_get_some():
    assert format_rules([], {}, config_path=None) == "\n".join([NO_CONFIG, "", NO_RULES])


def test_a_pack_without_rules_next_to_one_with_rules_needs_no_hint():
    text = format_rules([pack("empty", "./empty"), OTHER], {}, config_path=None)

    assert "No rules are loaded" not in text
    assert "  (no rules)" in text
