"""Shared test helpers for extracting raw callables out of a tool module's register(mcp) closures."""
import asyncio
import functools
import inspect

from unittest.mock import MagicMock

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError


def _sync(fn, raise_errors=False):
    """Tools are async (they run the blocking cloud SDK call in a worker thread). Tests are
    plain sync functions, so adapt: run the coroutine to completion.

    A failing tool raises mcp's ToolError (which a real MCP client receives as isError=True;
    that contract is verified end to end in tests/test_errors.py). By default this adapter
    returns the error message as a string so tests can assert on the message text; pass
    raise_errors=True to get the ToolError itself."""
    if not inspect.iscoroutinefunction(fn):
        return fn

    @functools.wraps(fn)
    def run(*args, **kwargs):
        try:
            return asyncio.run(fn(*args, **kwargs))
        except ToolError as e:
            if raise_errors:
                raise
            return f"Error: {e}"
    return run


def get_tool(module, tool_name: str, raise_errors=False):
    """Register a tool module's tools onto a fresh FastMCP instance and return the Python
    callable for one of them (bypassing MCP protocol framing entirely)."""
    mcp = FastMCP("test")
    module.register(mcp)
    return _sync(mcp._tool_manager._tools[tool_name].fn, raise_errors)


def get_all_tools(module, raise_errors=False) -> dict:
    """Same as get_tool, but returns every registered tool name mapped to its callable."""
    mcp = FastMCP("test")
    module.register(mcp)
    return {name: _sync(t.fn, raise_errors) for name, t in mcp._tool_manager._tools.items()}


def get_mcp_tools(module) -> dict:
    """The registered FastMCP Tool objects (for checking annotations/metadata)."""
    mcp = FastMCP("test")
    module.register(mcp)
    return dict(mcp._tool_manager._tools)


def mock_paginators(client, **operations):
    """Make `client.get_paginator(op).paginate(**kw)` yield the given pages.
    Usage: mock_paginators(mock_athena, list_databases=[{"DatabaseList": [...]}, ...])
    Records the kwargs of each paginate() call on client.paginate_calls[op]."""
    client.paginate_calls = {}

    def get_paginator(op):
        paginator = MagicMock()

        def paginate(**kwargs):
            client.paginate_calls.setdefault(op, []).append(kwargs)
            return iter(operations[op])
        paginator.paginate.side_effect = paginate
        return paginator

    client.get_paginator.side_effect = get_paginator
    return client
