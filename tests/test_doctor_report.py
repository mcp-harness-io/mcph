import asyncio
from pathlib import Path

from mcph import doctor
from mcph.config import Config, ServerConfig
from mcph.doctor import DoctorReport, ServerReport, format_report, run_doctor
from mcph.mcp_client import ServerError, ServerSnapshot, ToolInfo


def snapshot(
    *,
    name="orders-server",
    version="1.2.3",
    protocol="2026-07-28",
    seconds=0.3124,
    tools=("get_order", "list_orders"),
) -> ServerSnapshot:
    return ServerSnapshot(
        server_name=name,
        server_version=version,
        protocol_version=protocol,
        startup_seconds=seconds,
        tools=tuple(ToolInfo(tool) for tool in tools),
    )


def test_report_for_a_healthy_and_a_failed_server():
    report = DoctorReport(
        (
            ServerReport("orders", snapshot=snapshot()),
            ServerReport(
                "legacy",
                error=ServerError(
                    "failed while starting the server: Connection closed",
                    "Traceback (most recent call last):\nboom",
                ),
            ),
        )
    )

    assert format_report(report, config_path=Path("/p/mcph.yaml")) == "\n".join(
        [
            "Checking 2 servers from /p/mcph.yaml",
            "",
            "orders",
            "  OK     started in 312 ms (orders-server 1.2.3, MCP 2026-07-28), 2 tools",
            "legacy",
            "  ERROR  failed while starting the server: Connection closed",
            "         server stderr:",
            "           Traceback (most recent call last):",
            "           boom",
            "",
            "1 of 2 servers failed.",
        ]
    )


def test_report_for_a_single_healthy_server_without_a_config_path():
    report = DoctorReport((ServerReport("orders", snapshot=snapshot(tools=("a",))),))

    assert format_report(report) == "\n".join(
        [
            "Checking 1 server",
            "",
            "orders",
            "  OK     started in 312 ms (orders-server 1.2.3, MCP 2026-07-28), 1 tool",
            "",
            "All servers OK.",
        ]
    )


def test_report_leaves_out_details_the_server_did_not_provide():
    bare = snapshot(name=None, version=None, protocol=None, seconds=0.005, tools=())
    report = DoctorReport((ServerReport("orders", snapshot=bare),))

    assert "  OK     started in 5 ms, 0 tools" in format_report(report)


def test_report_shows_only_the_protocol_when_the_server_gave_no_name():
    report = DoctorReport(
        (ServerReport("orders", snapshot=snapshot(name=None, version=None)),)
    )

    assert "started in 312 ms (MCP 2026-07-28)" in format_report(report)


def test_report_for_an_error_without_stderr_has_no_stderr_section():
    report = DoctorReport(
        (ServerReport("orders", error=ServerError("timed out after 1 s while starting the server")),)
    )

    text = format_report(report)

    assert "  ERROR  timed out after 1 s while starting the server" in text
    assert "server stderr" not in text
    assert "1 of 1 server failed." in text


def test_doctor_report_is_ok_only_if_every_server_is_ok():
    healthy = ServerReport("a", snapshot=snapshot())
    broken = ServerReport("b", error=ServerError("nope"))

    assert DoctorReport((healthy,)).ok
    assert not DoctorReport((healthy, broken)).ok


def test_run_doctor_checks_every_server_in_order_and_survives_failures(monkeypatch):
    calls = []

    async def fake_snapshot(server, *, cwd, timeout):
        calls.append((server.name, cwd, timeout))
        if server.name == "broken":
            raise ServerError("boom")
        return snapshot()

    monkeypatch.setattr(doctor, "snapshot_stdio_server", fake_snapshot)
    config = Config(
        servers=[
            ServerConfig(name=name, transport="stdio", command="x")
            for name in ("first", "broken", "last")
        ]
    )

    report = asyncio.run(run_doctor(config, timeout=7))

    assert [server.name for server in report.servers] == ["first", "broken", "last"]
    assert [server.ok for server in report.servers] == [True, False, True]
    assert report.servers[1].error.message == "boom"
    assert calls == [(name, config.base_dir, 7) for name in ("first", "broken", "last")]
