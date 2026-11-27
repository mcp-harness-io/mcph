from pathlib import Path

from mcph.extensions import (
    LoadedPack,
    PackManifest,
    PackReport,
    format_packs,
    format_verification,
)
from mcph.rules import Rule, Severity

NO_PACKS = (
    "No packs are loaded: add a pack to `extends` in mcph.yaml "
    "(`extends: [builtin:core]` has the rules that ship with mcph)."
)


def rule(rule_id: str, description: str, severity: Severity = Severity.WARN) -> Rule:
    return Rule(rule_id, description, severity, lambda context: [])


def pack(name: str, source: str, *rules: Rule, version="1.0.0", description="") -> LoadedPack:
    manifest = PackManifest(name=name, version=version, description=description)
    return LoadedPack(manifest, source, Path("/packs") / name, tuple(rules))


ACME = pack(
    "acme",
    "./packs/acme",
    rule("acme.no_delete", "No tool deletes data.", Severity.ERROR),
    rule("acme.names", "Names are fine."),
    description="Our checks.",
)


# --- the listing ------------------------------------------------------------------------------


def test_each_pack_is_listed_with_its_version_source_description_and_rule_ids():
    other = pack("core", "builtin:core", rule("core.a", "A."), version="2.0.1")

    text = format_packs([ACME, other], config_path=Path("/p/mcph.yaml"))

    assert text.splitlines() == [
        "Packs (from /p/mcph.yaml)",
        "",
        "acme 1.0.0 (./packs/acme)",
        "  Our checks.",
        "  acme.no_delete",
        "  acme.names",
        "",
        "core 2.0.1 (builtin:core)",
        "  core.a",
    ]


def test_a_pack_without_a_description_or_rules_says_so():
    text = format_packs([pack("empty", "./empty")], config_path=Path("/p/mcph.yaml"))

    assert text.splitlines()[2:] == ["empty 1.0.0 (./empty)", "  (no rules)"]


def test_without_any_pack_the_listing_says_how_to_get_some():
    assert format_packs([], config_path=None) == "\n".join(
        ["Packs (no mcph.yaml in the current directory)", "", NO_PACKS]
    )
    assert format_packs([], config_path=Path("/p/mcph.yaml")).splitlines() == [
        "Packs (from /p/mcph.yaml)",
        "",
        NO_PACKS,
    ]


def test_the_packs_are_listed_in_the_order_given():
    other = pack("zeta", "./zeta")

    text = format_packs([other, ACME], config_path=None)

    assert text.index("zeta 1.0.0") < text.index("acme 1.0.0")


# --- the verification -------------------------------------------------------------------------


def test_a_valid_pack_is_listed_with_its_rules_severities_and_descriptions():
    text = format_verification(PackReport(ACME, ()), path="./packs/acme")

    assert text.splitlines() == [
        "acme 1.0.0: valid, 2 rules",
        "  acme.no_delete  error  No tool deletes data.",
        "  acme.names      warn   Names are fine.",
    ]


def test_a_valid_pack_with_one_rule_or_none_uses_the_right_plural():
    one = format_verification(PackReport(pack("a", "x", rule("a.r", "R.")), ()), path=".")
    none = format_verification(PackReport(pack("a", "x"), ()), path=".")

    assert one.splitlines()[0] == "a 1.0.0: valid, 1 rule"
    assert none == "a 1.0.0: valid, 0 rules"


def test_problems_are_listed_under_the_path_one_each():
    report = PackReport(ACME, ("first problem", "second problem"))

    assert format_verification(report, path="./packs/acme").splitlines() == [
        "./packs/acme: 2 problems",
        "  first problem",
        "  second problem",
    ]


def test_a_single_problem_uses_the_singular_and_multi_line_problems_stay_indented():
    report = PackReport(None, ("invalid pack manifest /p/mcph-pack.yaml:\n  name: Field required",))

    assert format_verification(report, path=".").splitlines() == [
        ".: 1 problem",
        "  invalid pack manifest /p/mcph-pack.yaml:",
        "    name: Field required",
    ]
