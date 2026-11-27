import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

import mcph.extensions
from mcph.rules import Finding, Severity, run_rule

CORE = Path(mcph.extensions.__file__).parent / "builtin_packs" / "core"


def import_rule_file(stem: str):
    """Import a rule file of the core pack as a module, to test what it defines besides rules."""
    spec = importlib.util.spec_from_file_location(f"core_pack_test_{stem}", CORE / "rules" / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


to_snake_case = import_rule_file("tool_name_snake_case").to_snake_case


@pytest.fixture
def rule(core_rules):
    return core_rules["core.tool_name_snake_case"]


@pytest.fixture
def check(rule, context_with):
    """Run only this rule against a server that has tools with the given names."""

    def run(*tool_names: str) -> list[Finding]:
        return list(asyncio.run(run_rule(rule, context_with(*tool_names))).findings)

    return run


def test_the_rule_is_a_warning_by_default(rule):
    assert rule.id == "core.tool_name_snake_case"
    assert rule.severity is Severity.WARN


@pytest.mark.parametrize(
    "name",
    ["get_order", "search", "list_cves_v2", "a1", "x", "v2_search", "2fa_verify", "get_order_2"],
)
def test_snake_case_names_are_accepted(check, name):
    assert check(name) == []


@pytest.mark.parametrize(
    ("name", "suggestion"),
    [
        ("getOrder", "get_order"),
        ("GetOrder", "get_order"),
        ("get-order", "get_order"),
        ("get_Order", "get_order"),
        ("GET_ORDER", "get_order"),
        ("HTTPServer", "http_server"),
        ("getHTTPResponse", "get_http_response"),
        ("list orders", "list_orders"),
        ("orders.get", "orders_get"),
        ("get__order", "get_order"),
        ("_private_tool", "private_tool"),
        ("get_order_", "get_order"),
    ],
)
def test_other_names_get_a_warning_with_a_suggestion(check, name, suggestion):
    [finding] = check(name)

    assert finding == Finding(
        rule_id="core.tool_name_snake_case",
        severity=Severity.WARN,
        message=f"tool {name!r} is not snake_case (try {suggestion!r})",
    )


@pytest.mark.parametrize("name", ["café_tool", "---", "日本語"])
def test_no_suggestion_when_no_valid_snake_case_form_exists(check, name):
    [finding] = check(name)

    assert finding.message == f"tool {name!r} is not snake_case"


def test_only_the_offending_tools_are_reported_in_order(check):
    findings = check("get_order", "getShipment", "ok_tool", "List-All")

    assert [finding.message for finding in findings] == [
        "tool 'getShipment' is not snake_case (try 'get_shipment')",
        "tool 'List-All' is not snake_case (try 'list_all')",
    ]


def test_a_server_without_tools_has_no_findings(check):
    assert check() == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [("getOrder", "get_order"), ("already_snake", "already_snake"), ("", ""), ("--", "")],
)
def test_to_snake_case(name, expected):
    assert to_snake_case(name) == expected
