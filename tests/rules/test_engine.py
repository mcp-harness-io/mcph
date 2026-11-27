import asyncio

import pytest

from mcph.mcp_client import ServerError
from mcph.rules import (
    NO_CONNECTION,
    Finding,
    Issue,
    Outcome,
    Rule,
    RuleContext,
    RuleInfo,
    RuleResult,
    Severity,
    SkipRule,
    run_rule,
)

TIMEOUT = 5.0


def run(rule: Rule, context: RuleContext) -> RuleResult:
    return asyncio.run(run_rule(rule, context))


def no_delete(severity=Severity.ERROR) -> Rule:
    return Rule(
        id="acme.no_delete",
        description="No delete tools.",
        severity=severity,
        check=lambda context: [
            f"{tool.name} deletes data"
            for tool in context.snapshot.tools
            if tool.name.startswith("delete")
        ],
    )


def test_the_findings_of_a_rule_carry_its_id_and_severity(context_with):
    result = run(no_delete(), context_with("get_x", "delete_x", "delete_y"))

    assert result == RuleResult(
        "acme.no_delete",
        findings=(
            Finding("acme.no_delete", Severity.ERROR, "delete_x deletes data"),
            Finding("acme.no_delete", Severity.ERROR, "delete_y deletes data"),
        ),
    )
    assert result.outcome is Outcome.RAN


def test_a_rule_that_finds_nothing_has_a_result_without_findings(context_with):
    assert run(no_delete(), context_with("get_x")) == RuleResult("acme.no_delete")


def test_an_issue_can_carry_its_own_severity(context_with):
    rule = Rule(
        "a.rule",
        "",
        Severity.ERROR,
        lambda context: [
            Issue("serious"),
            Issue("minor", Severity.WARN),
            "also serious",
        ],
    )

    result = run(rule, context_with())

    assert [(f.severity, f.message) for f in result.findings] == [
        (Severity.ERROR, "serious"),
        (Severity.WARN, "minor"),
        (Severity.ERROR, "also serious"),
    ]


def test_a_check_may_be_async(context_with):
    async def check(context):
        await asyncio.sleep(0)
        return ["found after waiting"]

    result = run(Rule("a.rule", "", Severity.WARN, check), context_with())

    assert [f.message for f in result.findings] == ["found after waiting"]


def test_a_check_may_be_a_generator(context_with):
    def check(context):
        yield "one"
        yield "two"

    result = run(Rule("a.rule", "", Severity.WARN, check), context_with())

    assert [f.message for f in result.findings] == ["one", "two"]


def test_a_rule_can_skip_itself_with_a_reason(context_with):
    def check(context):
        raise SkipRule("nothing to look at")

    result = run(Rule("a.rule", "", Severity.WARN, check), context_with())

    assert result == RuleResult.skipped("a.rule", "nothing to look at")


def test_a_rule_that_needs_the_server_is_skipped_when_it_could_not_be_reached(
    stdio_server,
):
    asked = []
    rule = Rule("a.rule", "", Severity.WARN, lambda context: asked.append(context) or ["x"])
    context = RuleContext(stdio_server, TIMEOUT, error=ServerError("boom"))

    result = run(rule, context)

    assert result == RuleResult.skipped("a.rule", NO_CONNECTION)
    assert asked == []  # it was not even run


def test_a_rule_that_does_not_need_the_server_runs_without_a_snapshot(stdio_server):
    rule = Rule(
        "a.rule",
        "",
        Severity.WARN,
        lambda context: [f"error was {context.error.message}"],
        needs_connection=False,
    )
    context = RuleContext(stdio_server, TIMEOUT, error=ServerError("boom"))

    assert [f.message for f in run(rule, context).findings] == ["error was boom"]


def test_a_rule_that_raises_is_reported_as_an_error_instead_of_ending_the_run(context_with):
    def check(context):
        raise ValueError("boom")

    result = run(Rule("a.rule", "", Severity.WARN, check), context_with())

    assert result == RuleResult(
        "a.rule",
        findings=(Finding("a.rule", Severity.ERROR, "the rule failed: ValueError: boom"),),
    )


def test_a_rule_that_fails_while_being_awaited_is_reported_too(context_with):
    async def check(context):
        raise RuntimeError("late")

    result = run(Rule("a.rule", "", Severity.WARN, check), context_with())

    assert result.findings[0].message == "the rule failed: RuntimeError: late"


def test_a_check_that_returns_something_else_than_messages_is_reported(context_with):
    rule = Rule("a.rule", "", Severity.WARN, lambda context: [42])

    result = run(rule, context_with())

    assert result.findings[0].severity is Severity.ERROR
    assert "a check returns messages or Issue objects, not int" in result.findings[0].message


@pytest.mark.parametrize(
    ("transports", "stdio_applies", "http_applies"),
    [
        (("stdio", "http"), True, True),
        (("http",), False, True),
        (("stdio",), True, False),
    ],
)
def test_a_rule_applies_to_the_kinds_of_server_it_is_about(
    stdio_server, http_server, transports, stdio_applies, http_applies
):
    rule = Rule("a.rule", "", Severity.WARN, lambda context: [], transports=transports)

    assert rule.applies_to(stdio_server) is stdio_applies
    assert rule.applies_to(http_server) is http_applies


def test_a_rule_applies_to_every_kind_of_server_by_default(stdio_server, http_server):
    rule = Rule("a.rule", "", Severity.WARN, lambda context: [])

    assert rule.applies_to(stdio_server) and rule.applies_to(http_server)
    assert rule.needs_connection


def test_each_context_has_its_own_cache(stdio_server):
    first = RuleContext(stdio_server, TIMEOUT)
    second = RuleContext(stdio_server, TIMEOUT)

    first.cache["key"] = "value"

    assert second.cache == {}


def test_a_rule_is_what_it_is_called_plus_how_to_check_it():
    rule = Rule("a.rule", "Does a thing.", Severity.WARN, lambda context: [])

    assert isinstance(rule, RuleInfo)
    assert (rule.id, rule.description, rule.severity) == ("a.rule", "Does a thing.", Severity.WARN)


def test_a_rule_that_did_not_run_says_why_or_that_it_is_off():
    assert RuleResult.off("a.rule") == RuleResult("a.rule", Outcome.OFF, (), "")
    assert RuleResult.skipped("a.rule", "no server") == RuleResult(
        "a.rule", Outcome.SKIPPED, (), "no server"
    )
