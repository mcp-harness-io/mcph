import pytest

from mcph.mcp_client import ServerSnapshot, ToolInfo
from mcph.rules import (
    BUILTIN_RULES,
    TOOL_NAME_SNAKE_CASE,
    Finding,
    Rule,
    Severity,
    run_rules,
    to_snake_case,
)


def snapshot_with(*tool_names: str) -> ServerSnapshot:
    return ServerSnapshot(
        server_name="demo",
        server_version="1.0.0",
        protocol_version="2026-07-28",
        startup_seconds=0.1,
        tools=tuple(ToolInfo(name) for name in tool_names),
    )


def test_the_snake_case_rule_is_built_in_and_a_warning():
    assert BUILTIN_RULES == (TOOL_NAME_SNAKE_CASE,)
    assert TOOL_NAME_SNAKE_CASE.id == "core.tool_name_snake_case"
    assert TOOL_NAME_SNAKE_CASE.severity is Severity.WARN


@pytest.mark.parametrize(
    "name",
    ["get_order", "search", "list_cves_v2", "a1", "x", "v2_search", "2fa_verify", "get_order_2"],
)
def test_snake_case_names_are_accepted(name):
    assert run_rules(snapshot_with(name)) == []


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
def test_other_names_get_a_warning_with_a_suggestion(name, suggestion):
    [finding] = run_rules(snapshot_with(name))

    assert finding == Finding(
        rule_id="core.tool_name_snake_case",
        severity=Severity.WARN,
        message=f"tool {name!r} is not snake_case (try {suggestion!r})",
    )


@pytest.mark.parametrize("name", ["café_tool", "---", "日本語"])
def test_no_suggestion_when_no_valid_snake_case_form_exists(name):
    [finding] = run_rules(snapshot_with(name))

    assert finding.message == f"tool {name!r} is not snake_case"


def test_only_the_offending_tools_are_reported_in_order():
    findings = run_rules(snapshot_with("get_order", "getShipment", "ok_tool", "List-All"))

    assert [finding.message for finding in findings] == [
        "tool 'getShipment' is not snake_case (try 'get_shipment')",
        "tool 'List-All' is not snake_case (try 'list_all')",
    ]


def test_a_server_without_tools_has_no_findings():
    assert run_rules(snapshot_with()) == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [("getOrder", "get_order"), ("already_snake", "already_snake"), ("", ""), ("--", "")],
)
def test_to_snake_case(name, expected):
    assert to_snake_case(name) == expected


def test_run_rules_attaches_the_rule_id_and_severity_to_each_message():
    no_delete = Rule(
        id="acme.no_delete",
        description="No delete tools.",
        severity=Severity.ERROR,
        check=lambda snapshot: [
            f"{tool.name} deletes data"
            for tool in snapshot.tools
            if tool.name.startswith("delete")
        ],
    )

    findings = run_rules(snapshot_with("get_x", "delete_x", "delete_y"), [no_delete])

    assert findings == [
        Finding("acme.no_delete", Severity.ERROR, "delete_x deletes data"),
        Finding("acme.no_delete", Severity.ERROR, "delete_y deletes data"),
    ]


def test_run_rules_runs_every_rule_in_order():
    first = Rule("a.first", "", Severity.WARN, lambda snapshot: ["one"])
    second = Rule("a.second", "", Severity.ERROR, lambda snapshot: ["two"])

    findings = run_rules(snapshot_with("x"), [first, second])

    assert [(f.rule_id, f.message) for f in findings] == [("a.first", "one"), ("a.second", "two")]
