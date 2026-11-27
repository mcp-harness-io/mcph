from pathlib import Path

from mcph.doctor import format_rules
from mcph.rules import RuleInfo, Severity

RULES = [
    RuleInfo("core.short", "Short.", Severity.WARN),
    RuleInfo("oauth.a_longer_id", "Longer.", Severity.ERROR),
    RuleInfo("core.silent", "", Severity.WARN),
]
NO_CONFIG = "Rules (default settings: no mcph.yaml in the current directory)"


def test_without_a_config_every_rule_has_its_default_severity():
    assert format_rules(RULES, {}, config_path=None) == "\n".join(
        [
            NO_CONFIG,
            "",
            "  core.short         warn   Short.",
            "  oauth.a_longer_id  error  Longer.",
            "  core.silent        warn",
        ]
    )


def test_the_header_names_the_file_the_settings_come_from():
    text = format_rules(RULES, {}, config_path=Path("/p/mcph.yaml"))

    assert text.splitlines()[:2] == ["Rules (settings from /p/mcph.yaml)", ""]


def test_a_changed_rule_shows_its_default_and_the_columns_stay_aligned():
    settings = {"core.short": "error", "oauth.a_longer_id": "off"}

    text = format_rules(RULES, settings, config_path=Path("/p/mcph.yaml"))

    assert text.splitlines()[2:] == [
        "  core.short         error (default: warn)  Short.",
        "  oauth.a_longer_id  off (default: error)   Longer.",
        "  core.silent        warn",
    ]


def test_a_setting_equal_to_the_default_shows_no_default():
    settings = {"core.short": "warn", "oauth.a_longer_id": "error"}

    text = format_rules(RULES, settings, config_path=Path("/p/mcph.yaml"))

    assert "default:" not in text


def test_settings_for_rules_that_are_not_listed_are_ignored():
    text = format_rules(RULES[:1], {"core.other": "off"}, config_path=None)

    assert text.splitlines()[2:] == ["  core.short  warn  Short."]


def test_the_rules_are_listed_in_the_order_they_are_given():
    text = format_rules(reversed(RULES), {}, config_path=None)

    assert [line.split()[0] for line in text.splitlines()[2:]] == [
        "core.silent",
        "oauth.a_longer_id",
        "core.short",
    ]


def test_no_rules_leave_only_the_header():
    assert format_rules([], {}, config_path=None) == f"{NO_CONFIG}\n"
