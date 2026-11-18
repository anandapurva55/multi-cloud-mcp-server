"""Tool registration helper shared by every provider module."""
from mcp.types import ToolAnnotations

from providers.errors import friendly_errors

# Every tool here only reads cloud state. The hints let MCP clients skip confirmation
# prompts and tell users the server cannot modify their infrastructure.
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


def cloud_tool(mcp):
    """Decorator: register a sync function as a read-only MCP tool that runs in a
    worker thread and reports failures as isError results."""
    def decorator(func):
        return mcp.tool(annotations=READ_ONLY)(friendly_errors(func))
    return decorator
