import asyncio
import sys

import pytest

from mcph.extensions import (
    PackError,
    Registry,
    ResolvedPack,
    doctor_rule,
    load_pack,
    load_registry,
    resolve_pack,
)
from mcph.rules import Finding, RuleResult, Severity, run_rule


RULE = '''
from mcph.extensions import doctor_rule


@doctor_rule(id="acme.no_delete", description="No tool deletes data.", severity="error")
def no_delete(context):
    return [
        f"{tool.name} deletes data"
        for tool in context.snapshot.tools
        if tool.name.startswith("delete")
    ]
'''


def rule_file(*ids: str) -> str:
    """The source of a rule file that defines one rule per id (each reports one message)."""
    parts = ["from mcph.extensions import doctor_rule\n"]
    for rule_id in ids:
        function = rule_id.replace(".", "_").replace("-", "_")
        parts.append(
            f'\n@doctor_rule(id="{rule_id}", description="Rule {rule_id}.")\n'
            f'def {function}(context):\n    return ["{rule_id} found something"]\n'
        )
    return "".join(parts)


def load(directory) -> object:
    return load_pack(ResolvedPack(f"./{directory.name}", directory))


# --- the decorator ----------------------------------------------------------------------------


def test_the_decorator_returns_the_function_unchanged_and_attaches_the_rule():
    @doctor_rule(id="acme.thing", description="A thing.", severity="error")
    def check(context):
        return ["found"]

    assert check(None) == ["found"]
    rule = check.__mcph_rule__
    assert (rule.id, rule.description, rule.severity) == ("acme.thing", "A thing.", Severity.ERROR)
    assert rule.check is check
    assert rule.transports == ("stdio", "http") and rule.needs_connection


def test_the_defaults_are_a_warning_for_every_server_that_needs_a_connection():
    @doctor_rule(id="acme.thing", description="A thing.")
    def check(context):
        return []

    assert check.__mcph_rule__.severity is Severity.WARN


def test_the_severity_may_be_given_as_the_enum_and_the_other_options_are_kept():
    @doctor_rule(
        id="acme.thing",
        description="A thing.",
        severity=Severity.ERROR,
        transports=["http"],
        needs_connection=False,
    )
    def check(context):
        return []

    rule = check.__mcph_rule__
    assert rule.severity is Severity.ERROR
    assert rule.transports == ("http",)
    assert rule.needs_connection is False


@pytest.mark.parametrize("rule_id", ["acme.thing", "claude-code.max_depth", "a1.b2_c3", "9.9"])
def test_valid_rule_ids(rule_id):
    doctor_rule(id=rule_id, description="Fine.")(lambda context: [])


@pytest.mark.parametrize(
    "rule_id", ["thing", "Acme.thing", "acme.Thing", "acme.thing.more", "acme.", ".thing", "acme.a-b", ""]
)
def test_a_rule_id_must_look_like_namespace_dot_name(rule_id):
    with pytest.raises(ValueError, match="must look like 'namespace.name'"):
        doctor_rule(id=rule_id, description="Bad.")


@pytest.mark.parametrize("description", ["", "   "])
def test_a_rule_needs_a_description(description):
    with pytest.raises(ValueError, match="needs a description"):
        doctor_rule(id="acme.thing", description=description)


def test_the_severity_must_be_warn_or_error():
    with pytest.raises(ValueError, match="severity must be 'warn' or 'error', not 'fatal'"):
        doctor_rule(id="acme.thing", description="Bad.", severity="fatal")


@pytest.mark.parametrize("transports", [[], ["ssh"], ["stdio", "sse"]])
def test_transports_must_be_a_non_empty_subset_of_the_known_ones(transports):
    with pytest.raises(ValueError, match="transports must be a non-empty subset"):
        doctor_rule(id="acme.thing", description="Bad.", transports=transports)


def test_a_function_cannot_be_two_rules():
    @doctor_rule(id="acme.one", description="One.")
    def check(context):
        return []

    with pytest.raises(ValueError, match="check is already a rule"):
        doctor_rule(id="acme.two", description="Two.")(check)


# --- loading a pack ---------------------------------------------------------------------------


def test_a_pack_is_loaded_with_its_rules(write_pack, tmp_path):
    directory = write_pack(rules={"rules/no_delete.py": RULE}, manifest=None)

    pack = load_pack(resolve_pack("./acme", tmp_path))

    assert pack.manifest.name == "acme"
    assert pack.source == "./acme"
    assert pack.directory == directory
    [rule] = pack.rules
    assert (rule.id, rule.severity, rule.description) == (
        "acme.no_delete",
        Severity.ERROR,
        "No tool deletes data.",
    )


def test_a_pack_without_rule_files_has_no_rules(write_pack):
    pack = load(write_pack())

    assert pack.rules == ()


def test_a_rule_of_a_pack_runs_like_any_other_rule(write_pack, context):
    pack = load(write_pack(rules={"rules/no_delete.py": RULE}))
    [rule] = pack.rules

    result = asyncio.run(run_rule(rule, context("get_a", "delete_b")))

    assert result == RuleResult(
        "acme.no_delete",
        findings=(Finding("acme.no_delete", Severity.ERROR, "delete_b deletes data"),),
    )


def test_an_async_rule_of_a_pack_works(write_pack, context):
    source = """
    from mcph.extensions import doctor_rule


    @doctor_rule(id="acme.slow", description="Looks something up.")
    async def slow(context):
        return ["found late"]
    """
    [rule] = load(write_pack(rules={"rules/slow.py": source})).rules

    result = asyncio.run(run_rule(rule, context("a")))

    assert [f.message for f in result.findings] == ["found late"]


def test_rules_come_in_the_order_of_the_manifest_and_then_of_the_file(write_pack):
    pack = load(
        write_pack(
            rules={
                "rules/second.py": rule_file("acme.c", "acme.a"),
                "rules/first.py": rule_file("acme.b"),
            }
        )
    )

    assert [rule.id for rule in pack.rules] == ["acme.c", "acme.a", "acme.b"]


def test_an_alias_of_a_rule_function_counts_once(write_pack):
    source = rule_file("acme.thing") + "\nalias = acme_thing\n"

    pack = load(write_pack(rules={"rules/a.py": source}))

    assert [rule.id for rule in pack.rules] == ["acme.thing"]


def test_functions_that_are_not_marked_are_not_rules(write_pack):
    source = rule_file("acme.thing") + "\ndef helper(context):\n    return []\n\nCONSTANT = 3\n"

    pack = load(write_pack(rules={"rules/a.py": source}))

    assert [rule.id for rule in pack.rules] == ["acme.thing"]


def test_a_listed_file_that_does_not_exist(write_pack):
    directory = write_pack(rules={"rules/a.py": rule_file("acme.a")})
    (directory / "rules" / "a.py").unlink()

    with pytest.raises(PackError) as excinfo:
        load(directory)

    assert str(excinfo.value) == (
        "pack 'acme': rules/a.py, listed under rules in mcph-pack.yaml, does not exist"
    )


def test_a_file_that_defines_no_rule(write_pack):
    with pytest.raises(PackError) as excinfo:
        load(write_pack(rules={"rules/empty.py": "x = 1\n"}))

    assert str(excinfo.value) == (
        "pack 'acme': rules/empty.py defines no rule (mark each rule function with @doctor_rule)"
    )


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("def broken(:\n", "SyntaxError"),
        ("raise RuntimeError('boom')\n", "RuntimeError: boom"),
        ("import no_such_module_for_mcph\n", "ModuleNotFoundError"),
        ("import sys\nsys.exit(3)\n", "SystemExit: 3"),
        (
            "from mcph.extensions import doctor_rule\n"
            "@doctor_rule(id='Bad', description='x')\ndef f(context): return []\n",
            "ValueError: rule id 'Bad' must look like",
        ),
    ],
)
def test_a_file_that_cannot_be_imported_is_reported(write_pack, source, reason):
    with pytest.raises(PackError) as excinfo:
        load(write_pack(rules={"rules/bad.py": source}))

    message = str(excinfo.value)
    assert message.startswith("pack 'acme': cannot import bad.py: ")
    assert reason in message
    assert not [name for name in sys.modules if name.startswith("mcph_pack_acme_") and name.endswith("_bad")]


def test_the_same_rule_id_in_two_files_of_a_pack(write_pack):
    with pytest.raises(PackError) as excinfo:
        load(
            write_pack(
                rules={
                    "rules/a.py": rule_file("acme.thing"),
                    "rules/b.py": rule_file("acme.thing"),
                }
            )
        )

    assert str(excinfo.value) == (
        "pack 'acme': the rule id 'acme.thing' is defined in rules/a.py and in rules/b.py"
    )


def test_a_pack_with_an_invalid_manifest_is_not_loaded(write_pack):
    with pytest.raises(PackError, match="invalid pack manifest"):
        load(write_pack(manifest="version: '1'\n"))


def test_files_with_the_same_name_in_different_packs_do_not_clash(write_pack, tmp_path):
    first = load(write_pack("one", name="one", rules={"rules/a.py": rule_file("one.rule")}))
    second = load(write_pack("two", name="two", rules={"rules/a.py": rule_file("two.rule")}))

    assert [r.id for r in first.rules] == ["one.rule"]
    assert [r.id for r in second.rules] == ["two.rule"]
    assert first.rules[0].check is not second.rules[0].check


def test_a_dataclass_in_a_rule_file_works(write_pack, context):
    source = '''
    from __future__ import annotations

    from dataclasses import dataclass

    from mcph.extensions import doctor_rule


    @dataclass
    class Limit:
        count: int


    LIMIT = Limit(1)


    @doctor_rule(id="acme.max_tools", description="Not too many tools.")
    def max_tools(context):
        return ["too many"] if len(context.snapshot.tools) > LIMIT.count else []
    '''
    [rule] = load(write_pack(rules={"rules/limits.py": source})).rules

    assert asyncio.run(run_rule(rule, context("a", "b"))).findings[0].message == "too many"


# --- the registry -----------------------------------------------------------------------------


def test_the_registry_holds_the_packs_and_their_rules_in_load_order(write_pack):
    registry = Registry()
    first = load(write_pack("one", name="one", rules={"rules/a.py": rule_file("one.a", "one.b")}))
    second = load(write_pack("two", name="two", rules={"rules/a.py": rule_file("two.a")}))

    registry.add(first)
    registry.add(second)

    assert registry.packs == (first, second)
    assert [rule.id for rule in registry.rules] == ["one.a", "one.b", "two.a"]
    assert registry.pack_of("one.b") is first
    assert registry.pack_of("two.a") is second


def test_an_empty_registry(write_pack):
    registry = Registry()

    assert registry.packs == () and registry.rules == ()
    with pytest.raises(KeyError):
        registry.pack_of("acme.thing")


def test_two_packs_cannot_have_the_same_name(write_pack):
    registry = Registry()
    registry.add(load(write_pack("one", name="acme")))

    with pytest.raises(PackError) as excinfo:
        registry.add(load(write_pack("two", name="acme")))

    assert str(excinfo.value) == "two packs are called 'acme': './one' and './two'"


def test_two_packs_cannot_define_the_same_rule_and_the_registry_is_left_as_it_was(write_pack):
    registry = Registry()
    first = load(write_pack("one", name="one", rules={"rules/a.py": rule_file("shared.rule")}))
    registry.add(first)
    clash = load(
        write_pack("two", name="two", rules={"rules/a.py": rule_file("two.fine", "shared.rule")})
    )

    with pytest.raises(PackError) as excinfo:
        registry.add(clash)

    assert str(excinfo.value) == (
        "the rule 'shared.rule' is defined by two packs: 'one' ('./one') and 'two' ('./two')"
    )
    assert registry.packs == (first,)
    assert [rule.id for rule in registry.rules] == ["shared.rule"]  # nothing of 'two' was added


# --- loading from sources ---------------------------------------------------------------------


def test_load_registry_resolves_and_loads_the_sources_in_order(write_pack, tmp_path):
    write_pack("packs/one", name="one", rules={"rules/a.py": rule_file("one.a")})
    write_pack("packs/two", name="two", rules={"rules/a.py": rule_file("two.a")})

    registry = load_registry(["./packs/two", "./packs/one"], tmp_path)

    assert [pack.manifest.name for pack in registry.packs] == ["two", "one"]
    assert [pack.source for pack in registry.packs] == ["./packs/two", "./packs/one"]
    assert [rule.id for rule in registry.rules] == ["two.a", "one.a"]


def test_no_sources_give_an_empty_registry(tmp_path):
    registry = load_registry([], tmp_path)

    assert registry.packs == () and registry.rules == ()


def test_load_registry_stops_at_the_first_problem(write_pack, tmp_path):
    write_pack("packs/one", name="one")

    with pytest.raises(PackError, match="pack directory not found"):
        load_registry(["./packs/one", "./packs/missing", "./packs/other"], tmp_path)


def test_load_registry_uses_the_given_resolvers(write_pack, tmp_path):
    directory = write_pack("elsewhere", name="viaresolver")

    class Fixed:
        def resolve(self, source, base_dir):
            return ResolvedPack(source, directory)

    registry = load_registry(["whatever"], tmp_path, [Fixed()])

    assert [pack.manifest.name for pack in registry.packs] == ["viaresolver"]
    assert registry.packs[0].source == "whatever"
