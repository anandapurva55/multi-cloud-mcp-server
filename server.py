"""
Generic multi-cloud MCP server, GCP and AWS.

Config comes from .env (written by setup_wizard.py), not from source code.
Run the wizard first if you haven't:
    python setup_wizard.py

Then run the server:
    python server.py
"""
import sys

from mcp.server.fastmcp import FastMCP

from config import CONFIG, CONFIG_ERROR
from providers.tools import READ_ONLY

if CONFIG_ERROR:
    print(f"Invalid configuration: {CONFIG_ERROR}", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("multi-cloud-mcp")


@mcp.tool(annotations=READ_ONLY)
def list_environments() -> str:
    """List all configured GCP and AWS environment names, and which is the default for each."""
    lines = []
    if CONFIG.gcp_enabled:
        lines.append("GCP environments:")
        for name, env in CONFIG.gcp_environments.items():
            marker = " (default)" if name == CONFIG.gcp_default else ""
            lines.append(f"  • {name}{marker} - project: {env.project_id}, region: {env.region}")
    else:
        lines.append("GCP: not configured.")

    if CONFIG.aws_enabled:
        lines.append("AWS environments:")
        for name, env in CONFIG.aws_environments.items():
            marker = " (default)" if name == CONFIG.aws_default else ""
            lines.append(f"  • {name}{marker} - region: {env.region}")
    else:
        lines.append("AWS: not configured.")

    return "\n".join(lines)


if CONFIG.gcp_enabled:
    from providers.gcp import bigquery_tools, cloudbuild_tools, cloudrun_tools
    for module in (bigquery_tools, cloudbuild_tools, cloudrun_tools):
        module.register(mcp)

if CONFIG.aws_enabled:
    from providers.aws import athena_tools, codebuild_tools, ecs_tools, lambda_tools, s3_tools, identity_tools
    for module in (athena_tools, codebuild_tools, ecs_tools, lambda_tools, s3_tools, identity_tools):
        module.register(mcp)

if not CONFIG.gcp_enabled and not CONFIG.aws_enabled:
    print("No cloud providers configured. Run: python setup_wizard.py", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    mcp.run(transport="stdio")
