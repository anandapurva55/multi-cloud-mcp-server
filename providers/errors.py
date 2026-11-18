"""Shared error-handling and threading wrapper for MCP tool functions."""
import functools

import anyio.to_thread
from mcp.server.fastmcp.exceptions import ToolError


def friendly_errors(func):
    """Run a (blocking) tool function in a worker thread and turn failures into ToolError.

    * Threads: the cloud SDK calls are synchronous (and Athena polls with sleeps), so
      running them inline would freeze the whole stdio server (pings, cancellation,
      concurrent calls). anyio's worker threads keep the event loop free.
    * ToolError: FastMCP reports a raised ToolError to the client with isError=True,
      so clients can tell a failure ("expired SSO token") from real data. Returning an
      "Error: ..." string would be delivered as a successful result.
    """
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await anyio.to_thread.run_sync(functools.partial(func, *args, **kwargs))
        except ToolError:
            raise
        except ValueError as e:
            raise ToolError(str(e)) from e
        except Exception as e:
            raise ToolError(f"{type(e).__name__}: {e}") from e
    return wrapper
