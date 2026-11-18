"""Lambda tools, the serverless half of Cloud Run's niche: list/describe functions,
list versions, and fetch logs from the function's deterministic log group."""
import json

from config import CONFIG
from providers.aws.auth import get_client
from providers.aws.logs_helpers import DEFAULT_SINCE_MINUTES, fetch_log_events
from providers.aws.paging import list_note, paginate
from providers.tools import cloud_tool


def register(mcp):

    @cloud_tool(mcp)
    def lambda_list_functions(env: str = None) -> str:
        """List all Lambda functions. env: a configured AWS environment name (default: the configured default)."""
        aws_env = CONFIG.resolve_aws(env)
        client = get_client(aws_env, "lambda")
        functions, truncated = paginate(client, "list_functions", "Functions")
        if not functions:
            return "No Lambda functions found."
        rows = [{"name": f["FunctionName"], "runtime": f.get("Runtime"), "lastModified": f.get("LastModified")}
                for f in functions]
        return json.dumps(rows, indent=2, default=str) + list_note(len(rows), truncated)

    @cloud_tool(mcp)
    def lambda_describe_function(function_name: str, env: str = None) -> str:
        """
        Get full configuration of a Lambda function (runtime, memory, timeout, env vars, role).

        Args:
            function_name: e.g. 'my-function'
            env:            a configured AWS environment name (default: the configured default)
        """
        aws_env = CONFIG.resolve_aws(env)
        client = get_client(aws_env, "lambda")
        f = client.get_function(FunctionName=function_name)
        config = f["Configuration"]
        summary = {
            "name": config.get("FunctionName"),
            "runtime": config.get("Runtime"),
            "memorySize": config.get("MemorySize"),
            "timeout": config.get("Timeout"),
            "handler": config.get("Handler"),
            "role": config.get("Role"),
            "lastModified": config.get("LastModified"),
            "envVars": list(config.get("Environment", {}).get("Variables", {}).keys()),
        }
        return json.dumps(summary, indent=2, default=str)

    @cloud_tool(mcp)
    def lambda_list_versions(function_name: str, env: str = None) -> str:
        """
        List published versions of a Lambda function.

        Args:
            function_name: e.g. 'my-function'
            env:            a configured AWS environment name (default: the configured default)
        """
        aws_env = CONFIG.resolve_aws(env)
        client = get_client(aws_env, "lambda")
        versions, truncated = paginate(client, "list_versions_by_function", "Versions", FunctionName=function_name)
        if not versions:
            return f"No versions found for function '{function_name}'."
        rows = [{"version": v["Version"], "lastModified": v.get("LastModified"), "description": v.get("Description")}
                for v in versions]
        return json.dumps(rows, indent=2, default=str) + list_note(len(rows), truncated)

    @cloud_tool(mcp)
    def lambda_get_logs(function_name: str, limit: int = 50, since_minutes: int = DEFAULT_SINCE_MINUTES,
                        until_minutes: int = 0, env: str = None) -> str:
        """
        Get the most recent logs from a Lambda function within a time window (default: last 60 minutes).

        Args:
            function_name: e.g. 'my-function'
            limit:          Number of log entries to return (default 50, max 200).
            since_minutes:  Search window start, minutes ago (default 60, max 43200).
            until_minutes:  Search window end, minutes ago (default 0 = now).
            env:            a configured AWS environment name (default: the configured default)
        """
        aws_env = CONFIG.resolve_aws(env)
        logs_client = get_client(aws_env, "logs")
        return fetch_log_events(logs_client, f"/aws/lambda/{function_name}", limit, errors_only=False,
                                since_minutes=since_minutes, until_minutes=until_minutes)

    @cloud_tool(mcp)
    def lambda_get_error_logs(function_name: str, limit: int = 50, since_minutes: int = DEFAULT_SINCE_MINUTES,
                              until_minutes: int = 0, env: str = None) -> str:
        """
        Get only error-looking logs from a Lambda function within a time window (default: last 60 minutes).

        Args:
            function_name: e.g. 'my-function'
            limit:          Number of log entries to return (default 50, max 200).
            since_minutes:  Search window start, minutes ago (default 60, max 43200).
            until_minutes:  Search window end, minutes ago (default 0 = now).
            env:            a configured AWS environment name (default: the configured default)
        """
        aws_env = CONFIG.resolve_aws(env)
        logs_client = get_client(aws_env, "logs")
        return fetch_log_events(logs_client, f"/aws/lambda/{function_name}", limit, errors_only=True,
                                since_minutes=since_minutes, until_minutes=until_minutes)
