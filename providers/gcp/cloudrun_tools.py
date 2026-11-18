"""Cloud Run tools: list/describe services, list revisions, fetch logs (services and jobs)."""
import itertools
import json

from google.cloud import logging as gcp_logging
from google.cloud import run_v2

from config import CONFIG
from providers.gcp.auth import get_credentials
from providers.gcp.filters import quote, rfc3339
from providers.output import list_note
from providers.timewindow import DEFAULT_SINCE_MINUTES, validate_window
from providers.tools import cloud_tool

MAX_LIST_ITEMS = 200


def _region(gcp_env, region: str = None) -> str:
    """Cloud Run is regional: use the environment's region unless one is given."""
    return region or gcp_env.region


def _log_filter(service_name: str, errors_only: bool = False,
                since_minutes: int = DEFAULT_SINCE_MINUTES, until_minutes: int = 0) -> str:
    name = quote(service_name)
    filter_str = (
        f'((resource.type="cloud_run_revision" AND resource.labels.service_name={name}) '
        f'OR (resource.type="cloud_run_job" AND resource.labels.job_name={name}))'
        f' AND timestamp>="{rfc3339(since_minutes)}"'
    )
    if until_minutes:
        filter_str += f' AND timestamp<="{rfc3339(until_minutes)}"'
    if errors_only:
        filter_str += " AND severity>=ERROR"
    return filter_str


def _fetch_logs(gcp_env, service_name: str, limit: int, errors_only: bool,
                since_minutes: int, until_minutes: int) -> str:
    validate_window(since_minutes, until_minutes)
    limit = max(1, min(limit, 200))
    log_client = gcp_logging.Client(project=gcp_env.project_id, credentials=get_credentials(gcp_env))
    entries = list(log_client.list_entries(
        filter_=_log_filter(service_name, errors_only, since_minutes, until_minutes),
        order_by=gcp_logging.DESCENDING,
        max_results=limit,
    ))
    if not entries:
        return (f"No {'error ' if errors_only else ''}logs found for '{service_name}' "
                f"(Cloud Run service or job) in the selected time window.")
    rows = []
    for e in entries:
        rows.append({
            "timestamp": str(e.timestamp),
            "severity": e.severity,
            "message": e.payload if isinstance(e.payload, str) else json.dumps(e.payload, default=str),
        })
    return json.dumps(rows, indent=2, default=str)


def register(mcp):

    @cloud_tool(mcp)
    def cloudrun_list_services(region: str = None, env: str = None) -> str:
        """
        List Cloud Run services in one region (Cloud Run is regional, so other regions are not shown).

        Args:
            region: e.g. 'europe-west1' (default: the environment's configured region)
            env:    a configured GCP environment name (default: the configured default)
        """
        gcp_env = CONFIG.resolve_gcp(env)
        region = _region(gcp_env, region)
        client = run_v2.ServicesClient(credentials=get_credentials(gcp_env))
        parent = f"projects/{gcp_env.project_id}/locations/{region}"
        services = list(itertools.islice(client.list_services(parent=parent), MAX_LIST_ITEMS + 1))
        truncated = len(services) > MAX_LIST_ITEMS
        services = services[:MAX_LIST_ITEMS]
        if not services:
            return f"No Cloud Run services found in region '{region}'."
        rows = []
        for s in services:
            rows.append({
                "name": s.name.split("/")[-1],
                "url": s.uri,
                "latestRevision": s.latest_ready_revision.split("/")[-1] if s.latest_ready_revision else "",
                "updateTime": str(s.update_time),
            })
        return json.dumps(rows, indent=2, default=str) + list_note(len(rows), truncated)

    @cloud_tool(mcp)
    def cloudrun_describe_service(service_name: str, region: str = None, env: str = None) -> str:
        """
        Get full details of a Cloud Run service (URL, revision, traffic, resources).

        Args:
            service_name: e.g. 'my-service'
            region:       e.g. 'europe-west1' (default: the environment's configured region)
            env:          a configured GCP environment name (default: the configured default)
        """
        gcp_env = CONFIG.resolve_gcp(env)
        client = run_v2.ServicesClient(credentials=get_credentials(gcp_env))
        name = f"projects/{gcp_env.project_id}/locations/{_region(gcp_env, region)}/services/{service_name}"
        s = client.get_service(name=name)
        containers = s.template.containers
        summary = {
            "name": s.name.split("/")[-1],
            "url": s.uri,
            "latestRevision": s.latest_ready_revision.split("/")[-1] if s.latest_ready_revision else "",
            "updateTime": str(s.update_time),
            "containers": [
                {
                    "image": c.image,
                    "resources": {"cpu": c.resources.limits.get("cpu"), "memory": c.resources.limits.get("memory")},
                    "env": [e.name for e in c.env],
                }
                for c in containers
            ],
            "scaling": {"minInstances": s.template.scaling.min_instance_count,
                        "maxInstances": s.template.scaling.max_instance_count},
        }
        return json.dumps(summary, indent=2, default=str)

    @cloud_tool(mcp)
    def cloudrun_list_revisions(service_name: str, limit: int = 10, region: str = None, env: str = None) -> str:
        """
        List recent revisions of a Cloud Run service.

        Args:
            service_name: e.g. 'my-service'
            limit:        Number of revisions to return (default 10, max 50).
            region:       e.g. 'europe-west1' (default: the environment's configured region)
            env:          a configured GCP environment name (default: the configured default)
        """
        limit = max(1, min(limit, 50))
        gcp_env = CONFIG.resolve_gcp(env)
        client = run_v2.RevisionsClient(credentials=get_credentials(gcp_env))
        parent = f"projects/{gcp_env.project_id}/locations/{_region(gcp_env, region)}/services/{service_name}"
        revisions = list(itertools.islice(client.list_revisions(parent=parent), limit + 1))
        truncated = len(revisions) > limit
        revisions = revisions[:limit]
        if not revisions:
            return f"No revisions found for service '{service_name}'."
        rows = []
        for r in revisions:
            rows.append({
                "name": r.name.split("/")[-1],
                "createTime": str(r.create_time),
                "containers": [c.image for c in r.containers],
            })
        return json.dumps(rows, indent=2, default=str) + list_note(len(rows), truncated)

    @cloud_tool(mcp)
    def cloudrun_get_logs(service_name: str, limit: int = 50, since_minutes: int = DEFAULT_SINCE_MINUTES,
                          until_minutes: int = 0, env: str = None) -> str:
        """
        Get the most recent logs from a Cloud Run service or job within a time window (default: last 60 minutes).

        Args:
            service_name: e.g. 'my-service' (a Cloud Run service or job name)
            limit:        Number of log entries to return (default 50, max 200).
            since_minutes: Search window start, minutes ago (default 60, max 43200).
            until_minutes: Search window end, minutes ago (default 0 = now).
            env:          a configured GCP environment name (default: the configured default)
        """
        gcp_env = CONFIG.resolve_gcp(env)
        return _fetch_logs(gcp_env, service_name, limit, False, since_minutes, until_minutes)

    @cloud_tool(mcp)
    def cloudrun_get_error_logs(service_name: str, limit: int = 50, since_minutes: int = DEFAULT_SINCE_MINUTES,
                                until_minutes: int = 0, env: str = None) -> str:
        """
        Get only ERROR and CRITICAL logs from a Cloud Run service or job within a time window (default: last 60 minutes).

        Args:
            service_name: e.g. 'my-service' (a Cloud Run service or job name)
            limit:        Number of log entries to return (default 50, max 200).
            since_minutes: Search window start, minutes ago (default 60, max 43200).
            until_minutes: Search window end, minutes ago (default 0 = now).
            env:          a configured GCP environment name (default: the configured default)
        """
        gcp_env = CONFIG.resolve_gcp(env)
        return _fetch_logs(gcp_env, service_name, limit, True, since_minutes, until_minutes)
