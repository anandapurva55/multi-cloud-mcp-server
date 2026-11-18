"""CodeBuild tools: list projects/builds, fetch logs, describe build details."""
import json

from config import CONFIG
from providers.aws.auth import get_client
from providers.aws.logs_helpers import MAX_SINCE_MINUTES, fetch_log_events
from providers.aws.paging import list_note, paginate
from providers.tools import cloud_tool


def register(mcp):

    @cloud_tool(mcp)
    def codebuild_list_projects(env: str = None) -> str:
        """List all CodeBuild projects. env: a configured AWS environment name (default: the configured default)."""
        aws_env = CONFIG.resolve_aws(env)
        cb = get_client(aws_env, "codebuild")
        names, truncated = paginate(cb, "list_projects", "projects")
        if not names:
            return "No CodeBuild projects found."
        return "\n".join(f"  • {n}" for n in names) + list_note(len(names), truncated)

    @cloud_tool(mcp)
    def codebuild_list_builds(project_name: str, limit: int = 10, env: str = None) -> str:
        """
        List recent builds for a CodeBuild project.

        Args:
            project_name: e.g. 'my-service-build'
            limit:        Number of builds to return (default 10, max 50).
            env:          a configured AWS environment name (default: the configured default)
        """
        limit = max(1, min(limit, 50))
        aws_env = CONFIG.resolve_aws(env)
        cb = get_client(aws_env, "codebuild")
        build_ids, truncated = paginate(cb, "list_builds_for_project", "ids", limit=limit, projectName=project_name)
        if not build_ids:
            return f"No builds found for project '{project_name}'."
        builds = cb.batch_get_builds(ids=build_ids)["builds"]
        rows = []
        for b in builds:
            rows.append({
                "id": b.get("id"),
                "status": b.get("buildStatus"),
                "sourceVersion": b.get("sourceVersion"),
                "startTime": str(b.get("startTime")),
                "endTime": str(b.get("endTime")),
            })
        return json.dumps(rows, indent=2, default=str) + list_note(len(rows), truncated)

    @cloud_tool(mcp)
    def codebuild_get_logs(build_id: str, env: str = None) -> str:
        """
        Get the CloudWatch log output of a specific CodeBuild build.

        Args:
            build_id: The build ID (from codebuild_list_builds), e.g. 'my-project:abcd-1234'.
            env:      a configured AWS environment name (default: the configured default)
        """
        aws_env = CONFIG.resolve_aws(env)
        cb = get_client(aws_env, "codebuild")
        builds = cb.batch_get_builds(ids=[build_id])["builds"]
        if not builds:
            return f"Build '{build_id}' not found."
        log_info = builds[0].get("logs", {})
        group = log_info.get("groupName")
        if not group:
            return f"No CloudWatch log group recorded for build '{build_id}'.\nDeep link: {log_info.get('deepLink', '')}"
        logs_client = get_client(aws_env, "logs")
        stream = log_info.get("streamName")
        if stream:
            events = logs_client.get_log_events(logGroupName=group, logStreamName=stream, limit=200)["events"]
            if not events:
                return f"No log events found for build '{build_id}'."
            return "\n".join(e["message"].rstrip("\n") for e in events)
        return fetch_log_events(logs_client, group, limit=200, errors_only=False,
                                since_minutes=MAX_SINCE_MINUTES)

    @cloud_tool(mcp)
    def codebuild_describe_build(build_id: str, env: str = None) -> str:
        """
        Get full details (status, phases, timing, errors) of a CodeBuild build.

        Args:
            build_id: The build ID.
            env:      a configured AWS environment name (default: the configured default)
        """
        aws_env = CONFIG.resolve_aws(env)
        cb = get_client(aws_env, "codebuild")
        builds = cb.batch_get_builds(ids=[build_id])["builds"]
        if not builds:
            return f"Build '{build_id}' not found."
        b = builds[0]
        summary = {
            "id": b.get("id"),
            "status": b.get("buildStatus"),
            "startTime": str(b.get("startTime")),
            "endTime": str(b.get("endTime")),
            "sourceVersion": b.get("sourceVersion"),
            "phases": [
                {"phaseType": p.get("phaseType"), "phaseStatus": p.get("phaseStatus"),
                 "durationInSeconds": p.get("durationInSeconds")}
                for p in b.get("phases", [])
            ],
        }
        return json.dumps(summary, indent=2, default=str)
