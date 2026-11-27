"""``core.tool_name_snake_case``: tool names should be snake_case."""

from __future__ import annotations

import re
from collections.abc import Iterator

from mcph.extensions import doctor_rule
from mcph.rules import RuleContext

# Lowercase letters and digits, words separated by single underscores.
_SNAKE_CASE = re.compile(r"[a-z0-9]+(?:_[a-z0-9]+)*")


def to_snake_case(name: str) -> str:
    """Best-effort conversion to snake_case (``getOrder`` -> ``get_order``)."""
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)  # HTTPServer -> HTTP_Server
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)  # getOrder -> get_Order
    name = re.sub(r"\W+", "_", name)  # get-order, list orders -> get_order, ...
    return re.sub(r"_+", "_", name).strip("_").lower()


@doctor_rule(id="core.tool_name_snake_case", description="Tool names are snake_case.")
def tool_names_are_snake_case(context: RuleContext) -> Iterator[str]:
    assert context.snapshot is not None  # the rule needs the server to have connected
    for tool in context.snapshot.tools:
        if _SNAKE_CASE.fullmatch(tool.name):
            continue
        message = f"tool {tool.name!r} is not snake_case"
        suggestion = to_snake_case(tool.name)
        if _SNAKE_CASE.fullmatch(suggestion):
            message += f" (try {suggestion!r})"
        yield message
