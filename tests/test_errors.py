"""End-to-end tests of the tool error contract and threading, through a real in-memory MCP
client/server session (not the sync test adapter in tests/helpers.py).

* A failing tool must reach the client with isError=True (previously the decorator returned
  "Error: ..." strings, which clients received as successful results).
* Blocking cloud calls must not run on the event loop (previously an Athena poll could
  freeze the whole stdio server for up to 30s).
"""
import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from providers.aws import athena_tools, identity_tools, s3_tools
from providers.errors import friendly_errors
from providers.tools import READ_ONLY, cloud_tool


def run(coro):
    return asyncio.run(coro)


async def _call(server: FastMCP, name: str, args: dict):
    async with create_connected_server_and_client_session(server._mcp_server) as client:
        return await client.call_tool(name, args)


def _server_with(*funcs):
    mcp = FastMCP("t")
    for f in funcs:
        cloud_tool(mcp)(f)
    return mcp


def text_of(result):
    return "".join(c.text for c in result.content if hasattr(c, "text"))


def test_value_error_reaches_client_as_is_error_true():
    def boom() -> str:
        """boom"""
        raise ValueError("Unknown AWS environment 'x'. Configured: prod")

    result = run(_call(_server_with(boom), "boom", {}))
    assert result.isError is True
    assert "Unknown AWS environment 'x'. Configured: prod" in text_of(result)


def test_unexpected_exception_reaches_client_as_is_error_true_with_type_name():
    def expired() -> str:
        """expired"""
        raise RuntimeError("The SSO session has expired")

    result = run(_call(_server_with(expired), "expired", {}))
    assert result.isError is True
    assert "RuntimeError: The SSO session has expired" in text_of(result)


def test_successful_result_is_not_an_error():
    def fine(x: int = 2) -> str:
        """fine"""
        return f"x={x}"

    result = run(_call(_server_with(fine), "fine", {"x": 5}))
    assert result.isError is False
    assert text_of(result) == "x=5"


def test_real_tool_unknown_environment_is_is_error_true(aws_config):
    aws_config(identity_tools)
    mcp = FastMCP("t")
    identity_tools.register(mcp)
    result = run(_call(mcp, "aws_whoami", {"env": "nope"}))
    assert result.isError is True
    assert "Unknown AWS environment 'nope'" in text_of(result)


def test_real_tool_sql_guard_rejection_is_is_error_true(aws_config):
    aws_config(athena_tools)
    mcp = FastMCP("t")
    athena_tools.register(mcp)
    with patch("providers.aws.athena_tools.get_client", return_value=MagicMock()) as gc:
        result = run(_call(mcp, "athena_query", {"sql": "DROP TABLE orders"}))
    assert result.isError is True
    assert "read-only" in text_of(result).lower()
    gc.return_value.start_query_execution.assert_not_called()


def test_tool_runs_in_a_worker_thread_not_the_event_loop_thread():
    seen = {}

    def where() -> str:
        """where"""
        seen["thread"] = threading.current_thread()
        return "ok"

    async def main():
        loop_thread = threading.current_thread()
        await _call(_server_with(where), "where", {})
        return loop_thread

    loop_thread = run(main())
    assert seen["thread"] is not loop_thread


def test_a_slow_tool_does_not_block_the_event_loop():
    def slow() -> str:
        """slow"""
        time.sleep(0.6)   # stands in for an Athena poll loop / a hung API call
        return "done"

    async def main():
        stamps = []

        async def ticker():
            for _ in range(5):
                await anyio.sleep(0.1)
                stamps.append(time.monotonic())

        async with create_connected_server_and_client_session(_server_with(slow)._mcp_server) as client:
            start = time.monotonic()
            async with anyio.create_task_group() as tg:
                tg.start_soon(ticker)
                result = await client.call_tool("slow", {})
        return [start] + stamps, result

    stamps, result = run(main())
    assert text_of(result) == "done"
    # The ticker wants to fire every 0.1s. If the tool had run inline on the event loop, the first
    # tick would be stuck behind the 0.6s sleep, so the longest gap would be >= ~0.6s. (Measuring
    # gaps, not total spread: a blocked loop releases its backlog of ticks all at once afterwards.)
    longest_gap = max(b - a for a, b in zip(stamps, stamps[1:]))
    assert longest_gap < 0.35, f"event loop was blocked for {longest_gap:.2f}s"


def test_friendly_errors_preserves_signature_so_fastmcp_builds_the_right_schema():
    def tool(a: str, b: int = 3, env: str = None) -> str:
        """doc"""
        return "x"

    wrapped = friendly_errors(tool)
    mcp = FastMCP("t")
    mcp.tool()(wrapped)
    schema = mcp._tool_manager._tools["tool"].parameters
    assert set(schema["properties"]) == {"a", "b", "env"}
    assert schema["required"] == ["a"]
    assert mcp._tool_manager._tools["tool"].description == "doc"


def test_every_tool_is_declared_read_only():
    def t() -> str:
        """t"""
        return "x"

    mcp = _server_with(t)
    ann = mcp._tool_manager._tools["t"].annotations
    assert ann is not None
    assert ann.readOnlyHint is True and ann.destructiveHint is False
    assert ann == READ_ONLY


def test_annotations_are_advertised_to_clients_in_tools_list(aws_config):
    mcp = FastMCP("t")
    s3_tools.register(mcp)

    async def main():
        async with create_connected_server_and_client_session(mcp._mcp_server) as client:
            return await client.list_tools()

    tools = run(main()).tools
    assert {t.name for t in tools} == {"s3_list_buckets", "s3_list_objects"}
    for t in tools:
        assert t.annotations.readOnlyHint is True
        assert t.annotations.destructiveHint is False
