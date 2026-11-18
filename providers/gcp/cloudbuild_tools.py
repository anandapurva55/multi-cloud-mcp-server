"""Cloud Build tools: list builds, fetch logs, describe build details.

Cloud Build keeps builds in two places: the *global* collection (projects/{p}/builds,
what `gcloud builds list` shows and where default/global triggers write) and one
collection per *region* (projects/{p}/locations/{region}/builds). Each endpoint only
returns its own builds, so these tools consult both."""
import json

from config import CONFIG
from providers.gcp.auth import get_access_token
from providers.gcp.filters import quote
from providers.tools import cloud_tool

MAX_LOG_BYTES = 200_000  # tail of a build log returned to the model


class _NotFound(Exception):
    pass


def _request(path: str, env: str = None, params: dict = None) -> dict:
    import requests as _req
    gcp_env = CONFIG.resolve_gcp(env)
    token = get_access_token(gcp_env)
    url = f"https://cloudbuild.googleapis.com/v1/{path}"
    resp = _req.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=15)
    if resp.status_code == 404:
        raise _NotFound(path)
    resp.raise_for_status()
    return resp.json()


def _scopes(gcp_env, region: str = None) -> list:
    """Collections to search: global, plus the given/configured region ('global' = only global)."""
    base = f"projects/{gcp_env.project_id}"
    if (region or "").lower() == "global":
        return [("global", base)]
    region = region or gcp_env.region
    return [("global", base), (region, f"{base}/locations/{region}")]


def _get_build(gcp_env, build_id: str, env: str, region: str = None) -> dict:
    for _label, base in _scopes(gcp_env, region):
        try:
            return _request(f"{base}/builds/{build_id}", env=env)
        except _NotFound:
            continue
    places = ", ".join(label for label, _ in _scopes(gcp_env, region))
    raise ValueError(f"Build '{build_id}' not found (searched: {places}). "
                     "If it ran in another region, pass region='<that region>'.")


def register(mcp):

    @cloud_tool(mcp)
    def cloudbuild_list_builds(limit: int = 10, branch: str = "", region: str = None, env: str = None) -> str:
        """
        List recent Cloud Build builds (both global builds and builds in the environment's region).

        Args:
            limit:  Number of builds to return (default 10, max 50).
            branch: Filter by branch name (e.g. 'dev', 'qa', 'main'). Leave empty for all.
            region: Region to search in addition to global builds, or 'global' for global only
                    (default: the environment's configured region).
            env:    a configured GCP environment name (default: the configured default)
        """
        limit = max(1, min(limit, 50))
        gcp_env = CONFIG.resolve_gcp(env)
        params = {"pageSize": limit}
        if branch:
            params["filter"] = f"substitutions.BRANCH_NAME={quote(branch)}"
        builds, errors, searched = [], [], []
        for label, base in _scopes(gcp_env, region):
            try:
                builds.extend(_request(f"{base}/builds", env=env, params=params).get("builds", []))
                searched.append(label)
            except _NotFound:
                errors.append(f"{label}: not found")
            except Exception as e:
                errors.append(f"{label}: {type(e).__name__}: {e}")
        if errors and not searched:
            raise ValueError("Could not list builds: " + "; ".join(errors))
        unique = {b.get("id"): b for b in builds}.values()
        builds = sorted(unique, key=lambda b: b.get("createTime") or "", reverse=True)[:limit]
        note = f"\n[Searched: {', '.join(searched)}." + (f" Failed: {'; '.join(errors)}." if errors else "") + "]"
        if not builds:
            return "No builds found." + note
        rows = []
        for b in builds:
            rows.append({
                "id": b.get("id"),
                "status": b.get("status"),
                "branch": b.get("substitutions", {}).get("BRANCH_NAME", ""),
                "createTime": b.get("createTime"),
                "finishTime": b.get("finishTime"),
                "logUrl": b.get("logUrl"),
            })
        return json.dumps(rows, indent=2) + note

    @cloud_tool(mcp)
    def cloudbuild_get_logs(build_id: str, region: str = None, env: str = None) -> str:
        """
        Get the log output of a specific Cloud Build build from GCS (the last ~200 KB if it is larger).

        Args:
            build_id: The build ID (from cloudbuild_list_builds).
            region:   Region the build ran in, if not global or the environment's region.
            env:      a configured GCP environment name (default: the configured default)
        """
        gcp_env = CONFIG.resolve_gcp(env)
        b = _get_build(gcp_env, build_id, env, region)
        logs_bucket = b.get("logsBucket", "")
        log_url = b.get("logUrl", "")
        from google.cloud import storage as gcs
        from providers.gcp.auth import get_credentials
        try:
            bucket_name = logs_bucket.replace("gs://", "").rstrip("/")
            gcs_client = gcs.Client(project=gcp_env.project_id, credentials=get_credentials(gcp_env))
            blob = gcs_client.bucket(bucket_name).blob(f"log-{build_id}.txt")
            blob.reload()
            size = blob.size or 0
            start = max(0, size - MAX_LOG_BYTES)
            text = blob.download_as_bytes(start=start).decode("utf-8", errors="replace")
            if start:
                text = f"[Log is {size:,} bytes; showing the last {size - start:,}.]\n" + text
            return text
        except Exception as e:
            raise ValueError(f"Could not fetch logs from GCS: {type(e).__name__}: {e}. Log URL: {log_url}") from e

    @cloud_tool(mcp)
    def cloudbuild_describe_build(build_id: str, region: str = None, env: str = None) -> str:
        """
        Get full details (status, steps, timing, errors) of a Cloud Build build.

        Args:
            build_id: The build ID.
            region:   Region the build ran in, if not global or the environment's region.
            env:      a configured GCP environment name (default: the configured default)
        """
        gcp_env = CONFIG.resolve_gcp(env)
        b = _get_build(gcp_env, build_id, env, region)
        summary = {
            "id": b.get("id"),
            "status": b.get("status"),
            "createTime": b.get("createTime"),
            "finishTime": b.get("finishTime"),
            "logUrl": b.get("logUrl"),
            "substitutions": b.get("substitutions", {}),
            "steps": [
                {"id": s.get("id"), "name": s.get("name"), "status": s.get("status"), "timing": s.get("timing")}
                for s in b.get("steps", [])
            ],
        }
        return json.dumps(summary, indent=2)
