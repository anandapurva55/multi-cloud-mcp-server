"""ECS/Fargate tools, the container half of Cloud Run's niche: list clusters/services,
describe a service, list tasks, and fetch logs from the service's task definition."""
import json

from config import CONFIG
from providers.aws.auth import get_client
from providers.aws.logs_helpers import DEFAULT_SINCE_MINUTES, fetch_log_events
from providers.aws.paging import list_note, paginate
from providers.tools import cloud_tool


def _get_service(ecs, cluster: str, service_name: str) -> dict:
    """describe_services reports unknown services in `failures`, not as an exception."""
    resp = ecs.describe_services(cluster=cluster, services=[service_name])
    if resp["services"]:
        return resp["services"][0]
    reasons = ", ".join(f.get("reason", "unknown") for f in resp.get("failures", [])) or "not found"
    raise ValueError(f"Service '{service_name}' not found in cluster '{cluster}' ({reasons}).")


def _log_group_for_service(ecs, cluster: str, service_name: str) -> str:
    service = _get_service(ecs, cluster, service_name)
    task_def_arn = service["taskDefinition"]
    task_def = ecs.describe_task_definition(taskDefinition=task_def_arn)["taskDefinition"]
    for container in task_def.get("containerDefinitions", []):
        log_config = container.get("logConfiguration", {})
        if log_config.get("logDriver") == "awslogs":
            options = log_config.get("options", {})
            return options.get("awslogs-group")
    raise ValueError(f"Service '{service_name}' has no awslogs log driver configured.")


def register(mcp):

    @cloud_tool(mcp)
    def ecs_list_clusters(env: str = None) -> str:
        """List all ECS clusters. env: a configured AWS environment name (default: the configured default)."""
        aws_env = CONFIG.resolve_aws(env)
        ecs = get_client(aws_env, "ecs")
        arns, truncated = paginate(ecs, "list_clusters", "clusterArns")
        if not arns:
            return "No ECS clusters found."
        return "\n".join(f"  • {arn.split('/')[-1]}" for arn in arns) + list_note(len(arns), truncated)

    @cloud_tool(mcp)
    def ecs_list_services(cluster: str, env: str = None) -> str:
        """
        List all services in an ECS cluster.

        Args:
            cluster: cluster name or ARN, e.g. 'my-cluster'
            env:     a configured AWS environment name (default: the configured default)
        """
        aws_env = CONFIG.resolve_aws(env)
        ecs = get_client(aws_env, "ecs")
        arns, truncated = paginate(ecs, "list_services", "serviceArns", cluster=cluster)
        if not arns:
            return f"No services found in cluster '{cluster}'."
        return "\n".join(f"  • {arn.split('/')[-1]}" for arn in arns) + list_note(len(arns), truncated)

    @cloud_tool(mcp)
    def ecs_describe_service(cluster: str, service_name: str, env: str = None) -> str:
        """
        Get full details of an ECS service (task definition, desired/running count, deployments).

        Args:
            cluster:      cluster name or ARN
            service_name: e.g. 'my-service'
            env:          a configured AWS environment name (default: the configured default)
        """
        aws_env = CONFIG.resolve_aws(env)
        ecs = get_client(aws_env, "ecs")
        s = _get_service(ecs, cluster, service_name)
        summary = {
            "name": s.get("serviceName"),
            "status": s.get("status"),
            "taskDefinition": s.get("taskDefinition"),
            "desiredCount": s.get("desiredCount"),
            "runningCount": s.get("runningCount"),
            "pendingCount": s.get("pendingCount"),
            "launchType": s.get("launchType"),
            "deployments": [
                {"status": d.get("status"), "taskDefinition": d.get("taskDefinition"),
                 "desiredCount": d.get("desiredCount"), "runningCount": d.get("runningCount")}
                for d in s.get("deployments", [])
            ],
        }
        return json.dumps(summary, indent=2, default=str)

    @cloud_tool(mcp)
    def ecs_list_tasks(cluster: str, service_name: str, limit: int = 10, env: str = None) -> str:
        """
        List running/recent tasks for an ECS service.

        Args:
            cluster:      cluster name or ARN
            service_name: e.g. 'my-service'
            limit:        Number of tasks to return (default 10).
            env:          a configured AWS environment name (default: the configured default)
        """
        limit = max(1, min(limit, 50))
        aws_env = CONFIG.resolve_aws(env)
        ecs = get_client(aws_env, "ecs")
        arns, truncated = paginate(ecs, "list_tasks", "taskArns", limit=limit,
                                   cluster=cluster, serviceName=service_name)
        if not arns:
            return f"No tasks found for service '{service_name}'."
        tasks = ecs.describe_tasks(cluster=cluster, tasks=arns)["tasks"]
        rows = []
        for t in tasks:
            rows.append({
                "taskArn": t["taskArn"].split("/")[-1],
                "lastStatus": t.get("lastStatus"),
                "healthStatus": t.get("healthStatus"),
                "startedAt": str(t.get("startedAt")),
                "taskDefinitionArn": t.get("taskDefinitionArn"),
            })
        return json.dumps(rows, indent=2, default=str) + list_note(len(rows), truncated)

    @cloud_tool(mcp)
    def ecs_get_logs(cluster: str, service_name: str, limit: int = 50, since_minutes: int = DEFAULT_SINCE_MINUTES,
                     until_minutes: int = 0, env: str = None) -> str:
        """
        Get the most recent logs from an ECS service (resolved via its task definition's awslogs config).

        Args:
            cluster:      cluster name or ARN
            service_name: e.g. 'my-service'
            limit:        Number of log entries to return (default 50, max 200).
            since_minutes: Search window start, minutes ago (default 60, max 43200).
            until_minutes: Search window end, minutes ago (default 0 = now).
            env:          a configured AWS environment name (default: the configured default)
        """
        aws_env = CONFIG.resolve_aws(env)
        ecs = get_client(aws_env, "ecs")
        log_group = _log_group_for_service(ecs, cluster, service_name)
        logs_client = get_client(aws_env, "logs")
        return fetch_log_events(logs_client, log_group, limit, errors_only=False,
                                since_minutes=since_minutes, until_minutes=until_minutes)

    @cloud_tool(mcp)
    def ecs_get_error_logs(cluster: str, service_name: str, limit: int = 50, since_minutes: int = DEFAULT_SINCE_MINUTES,
                           until_minutes: int = 0, env: str = None) -> str:
        """
        Get only error-looking logs from an ECS service.

        Args:
            cluster:      cluster name or ARN
            service_name: e.g. 'my-service'
            limit:        Number of log entries to return (default 50, max 200).
            since_minutes: Search window start, minutes ago (default 60, max 43200).
            until_minutes: Search window end, minutes ago (default 0 = now).
            env:          a configured AWS environment name (default: the configured default)
        """
        aws_env = CONFIG.resolve_aws(env)
        ecs = get_client(aws_env, "ecs")
        log_group = _log_group_for_service(ecs, cluster, service_name)
        logs_client = get_client(aws_env, "logs")
        return fetch_log_events(logs_client, log_group, limit, errors_only=True,
                                since_minutes=since_minutes, until_minutes=until_minutes)
