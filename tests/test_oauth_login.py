import asyncio
import logging

import pytest

from mcph.config import HttpServerConfig
from mcph.oauth_login import (
    LoginError,
    _caused_by,
    _failure_message,
    _quiet_logging,
    login,
)
from mcph.token_store import TokenStore


def test_only_servers_that_use_oauth_can_be_logged_in_to(tmp_path):
    server = HttpServerConfig(name="docs", transport="http", url="http://x/mcp")

    with pytest.raises(LoginError, match="does not use `auth: {type: oauth}`"):
        asyncio.run(login(server, TokenStore(tmp_path)))


# --- failure messages -------------------------------------------------------------------------


def test_a_timeout_anywhere_in_the_chain_gives_the_timeout_message():
    try:
        try:
            raise TimeoutError("OAuth callback timed out after 2 seconds")
        except TimeoutError as timeout:
            raise RuntimeError("Client failed to connect: callback timed out") from timeout
    except RuntimeError as exc:
        wrapped = exc

    assert _failure_message(wrapped, 2) == (
        "timed out after 2 s waiting for the sign-in to finish"
    )


def test_a_timeout_inside_an_exception_group_is_found_too():
    group = ExceptionGroup("unhandled errors", [ExceptionGroup("inner", [TimeoutError()])])

    assert _caused_by(group, TimeoutError)
    assert "timed out after 7 s" in _failure_message(group, 7)


def test_other_errors_show_their_own_message_without_the_wrapper_prefix():
    exc = RuntimeError("Client failed to connect: All connection attempts failed")

    assert _failure_message(exc, 300) == "All connection attempts failed"


def test_the_first_real_error_in_a_group_is_shown():
    group = ExceptionGroup("unhandled errors", [ValueError("the redirect URI is not registered")])

    assert _failure_message(group, 300) == "the redirect URI is not registered"


def test_an_error_without_a_message_shows_its_type():
    assert _failure_message(KeyError(), 300) == "KeyError"


def test_exception_cycles_do_not_hang_the_search():
    first, second = RuntimeError("a"), RuntimeError("b")
    first.__cause__, second.__cause__ = second, first

    assert not _caused_by(first, TimeoutError)


# --- logging --------------------------------------------------------------------------------


def levels():
    return logging.getLogger("fastmcp").level, logging.getLogger("mcp.client.auth").level


def test_library_logging_is_quiet_only_while_logging_in():
    before = levels()

    with _quiet_logging():
        assert levels() == (logging.WARNING, logging.CRITICAL)

    assert levels() == before


def test_library_logging_is_restored_after_an_error():
    before = levels()

    with pytest.raises(RuntimeError):
        with _quiet_logging():
            raise RuntimeError("boom")

    assert levels() == before
